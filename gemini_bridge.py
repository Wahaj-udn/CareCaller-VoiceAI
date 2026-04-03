#!/usr/bin/env python3
"""Bridge Twilio Media Streams to Gemini Live API.

This script runs a WebSocket server that accepts Twilio media stream events and
forwards audio to Gemini Live, then sends Gemini audio responses back to Twilio.

Environment variables:
- GEMINI_API_KEY (required)
- GEMINI_MODEL (default: gemini-3.1-flash-live-preview)
- MISSION_PROMPT (optional, recommended)
- MISSION_PROMPT_FILE (optional path to mission text)
- MISSION_KICKOFF (default asks Gemini to greet and start mission)
- BRIDGE_HOST (default: 0.0.0.0)
- BRIDGE_PORT (default: 8765)
- BRIDGE_PATH (default: /media-stream)
- INTERRUPT_RMS_THRESHOLD (default: 700)
"""

from __future__ import annotations

import argparse
import asyncio
import audioop
import base64
import datetime as dt
import json
import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import websockets
from google import genai
from google.genai import errors as genai_errors
from google.genai import types
from dotenv import load_dotenv

load_dotenv()

LOGGER = logging.getLogger("gemini-bridge")

DEFAULT_MISSION = (
    "You are a friendly voice interviewer on a phone call. "
    "Your mission is to gather the required caller information naturally. "
    "Keep responses concise and conversational. "
    "If user interrupts, stop and listen. "
    "If the caller goes off-topic briefly, politely bring them back to your mission."
)


@dataclass
class StreamState:
    stream_sid: str = ""
    call_sid: str = ""
    model_is_speaking: bool = False
    conversation_file: str = ""
    call_start_monotonic: float = 0.0
    agent_turn_start_seconds: Optional[float] = None
    agent_turn_audio_seconds: float = 0.0
    patient_name: str = "Patient"
    awaiting_first_user_response: bool = True
    initial_agent_turns_sent: int = 0


def _parse_sample_rate(mime_type: Optional[str], default: int) -> int:
    if not mime_type:
        return default
    match = re.search(r"rate=(\d+)", mime_type)
    if not match:
        return default
    return int(match.group(1))


def twilio_payload_to_pcm16_16k(payload_b64: str) -> bytes:
    """Convert Twilio base64 μ-law 8k payload into 16-bit PCM @ 16kHz."""
    ulaw = base64.b64decode(payload_b64)
    pcm8 = audioop.ulaw2lin(ulaw, 2)
    pcm16k, _ = audioop.ratecv(pcm8, 2, 1, 8000, 16000, None)
    return pcm16k


def pcm_to_twilio_payload(pcm_bytes: bytes, input_rate: int) -> str:
    """Convert 16-bit PCM bytes to Twilio base64 μ-law 8k payload."""
    if input_rate != 8000:
        pcm_bytes, _ = audioop.ratecv(pcm_bytes, 2, 1, input_rate, 8000, None)
    ulaw = audioop.lin2ulaw(pcm_bytes, 2)
    return base64.b64encode(ulaw).decode("ascii")


def pcm16_rms(pcm_bytes: bytes) -> int:
    """Return RMS loudness for 16-bit PCM bytes."""
    if not pcm_bytes:
        return 0
    return int(audioop.rms(pcm_bytes, 2))


def _pcm_audio_duration_seconds(pcm_bytes: bytes, sample_rate: int) -> float:
    if not pcm_bytes or sample_rate <= 0:
        return 0.0
    # 16-bit PCM mono -> 2 bytes per sample.
    return (len(pcm_bytes) / 2.0) / float(sample_rate)


def _format_seconds_compact(seconds: float) -> str:
    text = f"{max(0.0, seconds):.3f}".rstrip("0").rstrip(".")
    return text if text else "0"


def load_mission_prompt() -> str:
    mission_file = os.getenv("MISSION_PROMPT_FILE", "").strip()
    if mission_file:
        path = Path(mission_file)
        if path.exists() and path.is_file():
            content = path.read_text(encoding="utf-8").strip()
            if content:
                return content
    mission_inline = os.getenv("MISSION_PROMPT", "").strip()
    if mission_inline:
        return mission_inline
    return DEFAULT_MISSION


def apply_prompt_template(text: str, patient_name: Optional[str] = None) -> str:
    resolved_name = (patient_name or "").strip() or "Patient"
    return text.replace("{patient_name}", resolved_name)


def _extract_custom_parameters(start_payload: dict) -> dict[str, str]:
    raw = start_payload.get("customParameters")
    if isinstance(raw, dict):
        return {str(k): str(v) for k, v in raw.items() if v is not None}
    if isinstance(raw, list):
        params: dict[str, str] = {}
        for item in raw:
            if isinstance(item, dict):
                name = str(item.get("name") or "").strip()
                value = str(item.get("value") or "").strip()
                if name:
                    params[name] = value
        return params
    return {}


class BridgeService:
    def __init__(
        self,
        gemini_api_key: str,
        model: str,
        mission_prompt: str,
        kickoff_prompt: str,
        interrupt_rms_threshold: int,
    ) -> None:
        self._client = genai.Client(api_key=gemini_api_key)
        self._model = model
        self._mission_prompt = mission_prompt
        self._kickoff_prompt = kickoff_prompt
        self._interrupt_rms_threshold = interrupt_rms_threshold
        self._conversation_log_file = os.getenv("CONVERSATION_LOG_FILE", "conversation.txt").strip() or "conversation.txt"
        self._conversation_dir = Path(os.getenv("CONVERSATION_DIR", "conversation")).resolve()
        self._conversation_per_call = self._is_truthy(os.getenv("CONVERSATION_PER_CALL", "true"))
        self._conversation_log_lock = asyncio.Lock()

    @staticmethod
    def _is_truthy(value: str) -> bool:
        return value.strip().lower() in {"1", "true", "yes", "on"}

    async def handle_ws(self, websocket):
        state = StreamState()
        client_addr = getattr(websocket, "remote_address", None)
        LOGGER.info("Twilio stream connected from %s", client_addr)

        force_english = self._is_truthy(os.getenv("FORCE_ENGLISH", "true"))
        system_instruction = self._mission_prompt
        if force_english:
            system_instruction = (
                f"{system_instruction}\n\n"
                "Language policy: Use English only. "
                "If the caller speaks another language, politely ask to continue in English."
            )

        config = {
            "response_modalities": ["AUDIO"],
            "system_instruction": system_instruction,
            "input_audio_transcription": {},
            "realtime_input_config": {
                "automatic_activity_detection": {
                    "prefix_padding_ms": int(os.getenv("VAD_PREFIX_PADDING_MS", "120")),
                    "silence_duration_ms": int(os.getenv("VAD_SILENCE_DURATION_MS", "450")),
                }
            },
        }

        async with self._client.aio.live.connect(model=self._model, config=config) as session:
            twilio_to_gemini_task = asyncio.create_task(
                self._forward_twilio_to_gemini(websocket, session, state)
            )
            gemini_to_twilio_task = asyncio.create_task(
                self._forward_gemini_to_twilio(websocket, session, state)
            )

            # Keep the call alive until Twilio ends the stream. Some Gemini receive
            # loops may end between turns; if that happens, restart the receiver.
            while True:
                done, _ = await asyncio.wait(
                    [twilio_to_gemini_task, gemini_to_twilio_task],
                    return_when=asyncio.FIRST_COMPLETED,
                )

                if twilio_to_gemini_task in done:
                    exc = twilio_to_gemini_task.exception()
                    if exc:
                        raise exc
                    gemini_to_twilio_task.cancel()
                    break

                if gemini_to_twilio_task in done:
                    exc = gemini_to_twilio_task.exception()
                    if exc:
                        if self._is_transient_live_disconnect(exc):
                            LOGGER.warning("Transient Gemini Live disconnect (%s); restarting receiver", exc)
                        else:
                            raise exc
                    LOGGER.info("Gemini receive loop ended; restarting receiver for next turn")
                    gemini_to_twilio_task = asyncio.create_task(
                        self._forward_gemini_to_twilio(websocket, session, state)
                    )

        LOGGER.info("Twilio stream disconnected call_sid=%s", state.call_sid or "unknown")

    async def _forward_twilio_to_gemini(self, websocket, session, state: StreamState) -> None:
        async for raw in websocket:
            message = json.loads(raw)
            event = message.get("event")

            if event == "start":
                start = message.get("start", {})
                state.stream_sid = start.get("streamSid", "")
                state.call_sid = start.get("callSid", "")
                state.conversation_file = self._build_conversation_file_path(state.call_sid)
                state.call_start_monotonic = asyncio.get_running_loop().time()
                custom_params = _extract_custom_parameters(start)
                state.patient_name = custom_params.get("patient_name", "").strip() or "Patient"
                LOGGER.info("Stream started call_sid=%s stream_sid=%s", state.call_sid, state.stream_sid)
                await self._append_conversation_line(
                    f"# call_sid={state.call_sid or 'unknown'}",
                    conversation_file=state.conversation_file,
                )
                await self._append_conversation_line(
                    f"# patient_name={state.patient_name}",
                    conversation_file=state.conversation_file,
                )
                await session.send_realtime_input(
                    text=(
                        "Call context: The patient name for this call is "
                        f"'{state.patient_name}'. Use this exact name in greetings and references."
                    )
                )
                await session.send_realtime_input(
                    text=(
                        "Strict turn-taking rule for this call: Ask exactly one question at a time and then stop "
                        "speaking to wait for the caller's response. Do not ask the next question until a caller "
                        "response is received. This especially applies to name confirmation: after asking \"Hi, is "
                        "this {patient_name}?\" you must wait for the caller's reply before continuing."
                    )
                )
                if self._kickoff_prompt:
                    await session.send_realtime_input(
                        text=apply_prompt_template(self._kickoff_prompt, patient_name=state.patient_name)
                    )
                continue

            if event == "media":
                media = message.get("media", {})
                track = media.get("track", "inbound")
                if track != "inbound":
                    continue
                payload_b64 = media.get("payload")
                if not payload_b64:
                    continue

                pcm16k = twilio_payload_to_pcm16_16k(payload_b64)

                if state.model_is_speaking and pcm16_rms(pcm16k) >= self._interrupt_rms_threshold:
                    await self._send_twilio_clear(websocket, state.stream_sid)
                    state.model_is_speaking = False

                await session.send_realtime_input(
                    audio=types.Blob(data=pcm16k, mime_type="audio/pcm;rate=16000")
                )
                continue

            if event == "stop":
                LOGGER.info("Stream stop event call_sid=%s", state.call_sid or "unknown")
                break

    async def _forward_gemini_to_twilio(self, websocket, session, state: StreamState) -> None:
        gemini_buffer = ""
        suppress_current_turn = False

        async for response in session.receive():
            content = response.server_content
            if not content:
                continue

            if content.input_transcription and content.input_transcription.text:
                # Log caller speech as soon as transcript text arrives to avoid
                # waiting for finalization and appearing "late" in logs.
                await self._emit_transcript("user", content.input_transcription.text, state)
                state.awaiting_first_user_response = False

            if content.output_transcription and content.output_transcription.text:
                gemini_buffer = self._append_transcript(gemini_buffer, content.output_transcription.text)

            if content.model_turn and content.model_turn.parts:
                for part in content.model_turn.parts:
                    if not part.inline_data or not part.inline_data.data:
                        continue

                    if state.awaiting_first_user_response and state.initial_agent_turns_sent >= 1:
                        suppress_current_turn = True
                        continue

                    input_rate = _parse_sample_rate(part.inline_data.mime_type, default=24000)
                    payload_b64 = pcm_to_twilio_payload(part.inline_data.data, input_rate=input_rate)
                    if not state.stream_sid:
                        continue

                    await websocket.send(
                        json.dumps(
                            {
                                "event": "media",
                                "streamSid": state.stream_sid,
                                "media": {"payload": payload_b64},
                            }
                        )
                    )
                    if state.agent_turn_start_seconds is None:
                        state.agent_turn_start_seconds = self._elapsed_call_seconds(state)
                    state.agent_turn_audio_seconds += _pcm_audio_duration_seconds(
                        part.inline_data.data,
                        input_rate,
                    )
                    state.model_is_speaking = True

            if content.interrupted or content.turn_complete or content.generation_complete:
                state.model_is_speaking = False
                if gemini_buffer:
                    if state.awaiting_first_user_response and state.initial_agent_turns_sent >= 1:
                        LOGGER.info("Suppressed unsolicited agent turn before first caller response")
                    else:
                        time_range = self._current_agent_time_range(state)
                        await self._emit_transcript("agent", gemini_buffer, state, time_range=time_range)
                        if state.awaiting_first_user_response:
                            state.initial_agent_turns_sent += 1
                    gemini_buffer = ""

                if suppress_current_turn:
                    await session.send_realtime_input(
                        text=(
                            "Do not continue speaking yet. Wait silently for the caller's response to your first "
                            "question before generating any next question or follow-up."
                        )
                    )
                    suppress_current_turn = False

                self._reset_agent_turn_timing(state)

        # Flush any remaining partial transcript chunks when receive loop exits.
        if gemini_buffer:
            time_range = self._current_agent_time_range(state)
            await self._emit_transcript("agent", gemini_buffer, state, time_range=time_range)
        self._reset_agent_turn_timing(state)

    @staticmethod
    def _append_transcript(buffer: str, chunk: str) -> str:
        if not chunk:
            return buffer
        return f"{buffer}{chunk}"

    @staticmethod
    def _normalize_transcript(text: str) -> str:
        clean_text = re.sub(r"\s+", " ", text).strip()
        return clean_text

    async def _emit_transcript(
        self,
        speaker: str,
        text: str,
        state: StreamState,
        time_range: Optional[tuple[float, float]] = None,
    ) -> None:
        clean_text = self._normalize_transcript(text)
        if not clean_text:
            return
        LOGGER.info("%s> %s", speaker, clean_text)
        line_prefix = ""
        if speaker == "agent" and time_range is not None:
            start_s, end_s = time_range
            line_prefix = f"[{_format_seconds_compact(start_s)}-{_format_seconds_compact(end_s)}] "
        await self._append_conversation_line(
            f"{line_prefix}{speaker}>{clean_text}",
            conversation_file=state.conversation_file,
        )

    @staticmethod
    def _elapsed_call_seconds(state: StreamState) -> float:
        if state.call_start_monotonic <= 0:
            return 0.0
        return max(0.0, asyncio.get_running_loop().time() - state.call_start_monotonic)

    def _current_agent_time_range(self, state: StreamState) -> tuple[float, float]:
        start = state.agent_turn_start_seconds
        if start is None:
            now = self._elapsed_call_seconds(state)
            return now, now

        if state.agent_turn_audio_seconds > 0:
            end = start + state.agent_turn_audio_seconds
        else:
            end = self._elapsed_call_seconds(state)

        return start, max(start, end)

    @staticmethod
    def _reset_agent_turn_timing(state: StreamState) -> None:
        state.agent_turn_start_seconds = None
        state.agent_turn_audio_seconds = 0.0

    def _build_conversation_file_path(self, call_sid: str) -> str:
        if not self._conversation_per_call:
            return self._conversation_log_file

        safe_call_sid = re.sub(r"[^A-Za-z0-9_-]+", "_", (call_sid or "unknown").strip()) or "unknown"
        timestamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        self._conversation_dir.mkdir(parents=True, exist_ok=True)
        return str((self._conversation_dir / f"{timestamp}_{safe_call_sid}.txt").resolve())

    async def _append_conversation_line(self, line: str, conversation_file: str) -> None:
        target_file = conversation_file or self._conversation_log_file
        async with self._conversation_log_lock:
            with open(target_file, "a", encoding="utf-8") as f:
                f.write(f"{line}\n")

    @staticmethod
    def _is_transient_live_disconnect(exc: Exception) -> bool:
        if isinstance(exc, genai_errors.APIError):
            msg = str(exc)
            return "1006" in msg or "abnormal closure" in msg.lower()
        return False

    async def _send_twilio_clear(self, websocket, stream_sid: str) -> None:
        if not stream_sid:
            return
        await websocket.send(json.dumps({"event": "clear", "streamSid": stream_sid}))


async def run_server(host: str, port: int, path: str, service: BridgeService) -> None:
    async def ws_handler(websocket):
        request_path = getattr(websocket, "path", "")
        if path and request_path and request_path != path:
            LOGGER.warning("Rejecting websocket path=%s expected=%s", request_path, path)
            await websocket.close(code=1008, reason="Invalid path")
            return
        await service.handle_ws(websocket)

    async with websockets.serve(ws_handler, host, port, ping_interval=20, max_size=2**22):
        LOGGER.info("Gemini bridge listening on ws://%s:%s%s", host, port, path)
        await asyncio.Future()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Twilio <-> Gemini Live websocket bridge")
    parser.add_argument("--host", default=os.getenv("BRIDGE_HOST", "0.0.0.0"), help="Bind host")
    parser.add_argument("--port", type=int, default=int(os.getenv("BRIDGE_PORT", "8765")), help="Bind port")
    parser.add_argument("--path", default=os.getenv("BRIDGE_PATH", "/media-stream"), help="Expected websocket path")
    parser.add_argument(
        "--model",
        default=os.getenv("GEMINI_MODEL", "gemini-3.1-flash-live-preview"),
        help="Gemini Live model",
    )
    parser.add_argument(
        "--interrupt-rms-threshold",
        type=int,
        default=int(os.getenv("INTERRUPT_RMS_THRESHOLD", "700")),
        help="RMS threshold for caller interruption detection",
    )
    parser.add_argument("--log-level", default=os.getenv("LOG_LEVEL", "INFO"), help="Logging level")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    if not api_key:
        raise SystemExit("Missing GEMINI_API_KEY environment variable.")

    mission_prompt = apply_prompt_template(load_mission_prompt(), patient_name="Patient")
    kickoff_prompt = os.getenv(
        "MISSION_KICKOFF",
        "Please greet the caller and begin your interview mission now.",
    ).strip()
    kickoff_prompt = apply_prompt_template(kickoff_prompt, patient_name="Patient")

    service = BridgeService(
        gemini_api_key=api_key,
        model=args.model,
        mission_prompt=mission_prompt,
        kickoff_prompt=kickoff_prompt,
        interrupt_rms_threshold=args.interrupt_rms_threshold,
    )

    try:
        asyncio.run(run_server(args.host, args.port, args.path, service))
    except KeyboardInterrupt:
        LOGGER.info("Gemini bridge stopped by user")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
