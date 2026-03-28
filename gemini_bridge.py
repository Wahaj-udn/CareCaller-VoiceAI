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
import json
import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import websockets
from google import genai
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


def load_mission_prompt() -> str:
    mission_file = os.getenv("MISSION_PROMPT_FILE", "").strip()
    if mission_file:
        path = Path(mission_file)
        if path.exists() and path.is_file():
            content = path.read_text(encoding="utf-8").strip()
            if content:
                return apply_prompt_template(content)
    mission_inline = os.getenv("MISSION_PROMPT", "").strip()
    if mission_inline:
        return apply_prompt_template(mission_inline)
    return apply_prompt_template(DEFAULT_MISSION)


def apply_prompt_template(text: str) -> str:
    patient_name = os.getenv("PATIENT_NAME", "Patient").strip() or "Patient"
    return text.replace("{patient_name}", patient_name)


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

    async def handle_ws(self, websocket):
        state = StreamState()
        client_addr = getattr(websocket, "remote_address", None)
        LOGGER.info("Twilio stream connected from %s", client_addr)

        config = {
            "response_modalities": ["AUDIO"],
            "system_instruction": self._mission_prompt,
        }

        async with self._client.aio.live.connect(model=self._model, config=config) as session:
            if self._kickoff_prompt:
                await session.send_realtime_input(text=self._kickoff_prompt)

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
                LOGGER.info("Stream started call_sid=%s stream_sid=%s", state.call_sid, state.stream_sid)
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
        async for response in session.receive():
            content = response.server_content
            if not content:
                continue

            if content.input_transcription and content.input_transcription.text:
                LOGGER.info("caller> %s", content.input_transcription.text)

            if content.output_transcription and content.output_transcription.text:
                LOGGER.info("gemini> %s", content.output_transcription.text)

            if content.model_turn and content.model_turn.parts:
                for part in content.model_turn.parts:
                    if not part.inline_data or not part.inline_data.data:
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
                    state.model_is_speaking = True

            if content.interrupted or content.turn_complete or content.generation_complete:
                state.model_is_speaking = False

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

    mission_prompt = load_mission_prompt()
    kickoff_prompt = os.getenv(
        "MISSION_KICKOFF",
        "Please greet the caller and begin your interview mission now.",
    ).strip()
    kickoff_prompt = apply_prompt_template(kickoff_prompt)

    service = BridgeService(
        gemini_api_key=api_key,
        model=args.model,
        mission_prompt=mission_prompt,
        kickoff_prompt=kickoff_prompt,
        interrupt_rms_threshold=args.interrupt_rms_threshold,
    )

    asyncio.run(run_server(args.host, args.port, args.path, service))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
