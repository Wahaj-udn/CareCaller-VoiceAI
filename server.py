#!/usr/bin/env python3
"""Twilio voice webhook server for Carecaller.

Endpoints:
- GET /health
- POST /voice/incoming
- POST /voice/outbound
- POST /voice/events
- POST /voice/recording

Environment variables:
- PORT (default: 5000)
- HOST (default: 0.0.0.0)
- MEDIA_STREAM_URL (optional): wss://... endpoint that receives Twilio media stream
- RECORDINGS_DIR (default: recordings)
- TWILIO_ACCOUNT_SID / TWILIO_AUTH_TOKEN (required to download recording MP3)
"""

from __future__ import annotations

import argparse
import base64
import datetime as dt
import json
import os
from pathlib import Path
import re
import sys
import threading
from urllib import error as urlerror
from urllib import request as urlrequest

from dotenv import load_dotenv
from flask import Flask, Response, jsonify, request
from twilio.twiml.voice_response import Connect, VoiceResponse
from extract_responses import extract_responses, parse_transcript
from final_transcript_builder import build_for_whisper_file
from normalize_transcript_with_gemini import normalize_transcript
from whisper_transcriber import transcribe_recording_file

load_dotenv()


def _is_truthy(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def _safe_token(value: str, fallback: str = "unknown") -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_-]+", "_", (value or "").strip())
    return cleaned or fallback


def _download_recording_mp3(recording_url: str, output_file: Path, account_sid: str, auth_token: str) -> None:
    output_file.parent.mkdir(parents=True, exist_ok=True)

    auth_bytes = f"{account_sid}:{auth_token}".encode("utf-8")
    auth_header = base64.b64encode(auth_bytes).decode("ascii")
    req = urlrequest.Request(
        recording_url,
        headers={"Authorization": f"Basic {auth_header}"},
        method="GET",
    )

    with urlrequest.urlopen(req, timeout=30) as response:
        content = response.read()
    output_file.write_bytes(content)


def _start_transcription_job(app: Flask, recording_file: Path) -> None:
    def _job() -> None:
        transcript_dir = Path(os.getenv("WHISPER_TRANSCRIPT_DIR", "whisper_transcript"))
        whisper_model = os.getenv("WHISPER_MODEL", "small").strip() or "small"
        whisper_device = os.getenv("WHISPER_DEVICE", "cuda").strip() or "cuda"
        whisper_compute_type = os.getenv("WHISPER_COMPUTE_TYPE", "float16").strip() or "float16"
        whisper_language = os.getenv("WHISPER_LANGUAGE", "en").strip() or "en"
        final_transcript_dir = Path(os.getenv("FINAL_TRANSCRIPT_DIR", "final_transcript"))
        normalized_transcript_dir = Path(os.getenv("NORMALIZED_TRANSCRIPT_DIR", "normalized_transcript"))
        qa_json_dir = Path(os.getenv("QA_JSON_DIR", "qa_json"))
        conversation_dir = Path(os.getenv("CONVERSATION_DIR", "conversation"))
        auto_build_final = _is_truthy(os.getenv("AUTO_BUILD_FINAL_TRANSCRIPT", "true"))
        auto_normalize_final = _is_truthy(os.getenv("AUTO_NORMALIZE_TRANSCRIPT", "true"))
        auto_save_qa_json = _is_truthy(os.getenv("AUTO_SAVE_QA_JSON", "true"))
        normalizer_model = os.getenv("TRANSCRIPT_NORMALIZER_MODEL", "gemini-2.5-flash").strip() or "gemini-2.5-flash"

        try:
            transcript_path = transcribe_recording_file(
                recording_file=recording_file,
                transcript_dir=transcript_dir,
                model_name=whisper_model,
                device=whisper_device,
                compute_type=whisper_compute_type,
                language=whisper_language,
            )
            app.logger.info("Saved whisper transcript to %s", transcript_path)

            if auto_build_final:
                final_path = build_for_whisper_file(
                    whisper_file=transcript_path,
                    conversation_dir=conversation_dir,
                    output_dir=final_transcript_dir,
                )
                if final_path is None:
                    app.logger.warning(
                        "Could not build final transcript for %s (no matching conversation file)",
                        transcript_path,
                    )
                else:
                    app.logger.info("Saved final transcript to %s", final_path)

                    if auto_normalize_final:
                        normalized_output = normalized_transcript_dir / f"{final_path.stem}.normalized.txt"
                        normalize_transcript(
                            input_file=final_path,
                            output_file=normalized_output,
                            model=normalizer_model,
                        )
                        app.logger.info("Saved normalized transcript to %s", normalized_output)

                        if auto_save_qa_json:
                            qa_json_dir.mkdir(parents=True, exist_ok=True)
                            qa_output = qa_json_dir / f"{final_path.stem}.qa.json"
                            parsed_turns = parse_transcript(
                                normalized_output.read_text(encoding="utf-8", errors="ignore")
                            )
                            responses = extract_responses(parsed_turns)
                            qa_output.write_text(
                                json.dumps(responses, indent=2),
                                encoding="utf-8",
                            )
                            app.logger.info("Saved question/answer JSON to %s", qa_output)
        except Exception as exc:
            app.logger.error("Whisper transcription failed for %s: %s", recording_file, exc)

    threading.Thread(target=_job, daemon=True, name="whisper-transcribe").start()


def create_app() -> Flask:
    app = Flask(__name__)

    @app.get("/health")
    def health() -> Response:
        return jsonify({"ok": True, "service": "carecaller-voice-webhook"})

    @app.post("/voice/incoming")
    def incoming_voice() -> Response:
        return _build_voice_twiml(stream_label="incoming")

    @app.post("/voice/outbound")
    def outbound_voice() -> Response:
        return _build_voice_twiml(stream_label="outbound")

    @app.post("/voice/events")
    def voice_events() -> Response:
        # Keep this lightweight. In the next step we can persist call state/event logs.
        event = dict(request.form)
        call_sid = event.get("CallSid", "unknown")
        status = event.get("CallStatus") or event.get("StreamEvent") or "unknown"
        app.logger.info("voice_event call_sid=%s status=%s", call_sid, status)
        return ("", 204)

    @app.post("/voice/recording")
    @app.post("/voice/recording/")
    def recording_events() -> Response:
        event = dict(request.form)
        call_sid = event.get("CallSid", "unknown")
        recording_sid = event.get("RecordingSid", "unknown")
        recording_status = (event.get("RecordingStatus") or "").strip().lower()
        recording_url_base = (event.get("RecordingUrl") or "").strip()

        app.logger.info(
            "recording_event call_sid=%s recording_sid=%s status=%s",
            call_sid,
            recording_sid,
            recording_status or "unknown",
        )

        if recording_status != "completed":
            return ("", 204)

        if not recording_url_base:
            app.logger.warning("recording_event missing RecordingUrl for call_sid=%s", call_sid)
            return ("missing RecordingUrl", 400)

        account_sid = os.getenv("TWILIO_ACCOUNT_SID", "").strip()
        auth_token = os.getenv("TWILIO_AUTH_TOKEN", "").strip()
        if not account_sid or not auth_token:
            app.logger.error("Cannot download recording: Twilio credentials are not configured")
            return ("twilio credentials not configured", 500)

        recordings_dir = Path(os.getenv("RECORDINGS_DIR", "recordings")).resolve()
        timestamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        safe_call_sid = _safe_token(call_sid, fallback="call")
        safe_recording_sid = _safe_token(recording_sid, fallback="recording")
        filename = f"{timestamp}_{safe_call_sid}_{safe_recording_sid}.mp3"
        output_file = recordings_dir / filename

        if output_file.exists():
            app.logger.info("Recording already exists, skipping download: %s", output_file)
            return ("", 204)

        recording_url_mp3 = f"{recording_url_base}.mp3"

        try:
            _download_recording_mp3(
                recording_url=recording_url_mp3,
                output_file=output_file,
                account_sid=account_sid,
                auth_token=auth_token,
            )
            app.logger.info("Saved call recording to %s", output_file)

            if _is_truthy(os.getenv("AUTO_TRANSCRIBE_RECORDINGS", "true")):
                _start_transcription_job(app, output_file)
        except (urlerror.URLError, TimeoutError, OSError) as exc:
            app.logger.error("Failed to download recording sid=%s: %s", recording_sid, exc)
            return ("failed to download recording", 502)

        return ("", 204)

    return app


def _build_voice_twiml(stream_label: str) -> Response:
    response = VoiceResponse()
    stream_url = os.getenv("MEDIA_STREAM_URL", "").strip()

    response.say(
        "Hi, this is Carecaller. We are connecting you to our AI interviewer now.",
        voice="alice",
    )

    if stream_url:
        connect = Connect()
        connect.stream(
            url=stream_url,
            name=f"carecaller-{stream_label}",
        )
        response.append(connect)
        response.pause(length=600)
    else:
        response.say(
            "Voice stream is not configured yet. Please set MEDIA_STREAM_URL and call again.",
            voice="alice",
        )
        response.hangup()

    xml = str(response)
    return Response(xml, mimetype="text/xml")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Carecaller Twilio webhook server")
    parser.add_argument("--host", default=os.getenv("HOST", "0.0.0.0"), help="Host interface")
    parser.add_argument("--port", type=int, default=int(os.getenv("PORT", "5000")), help="Port")
    parser.add_argument("--debug", action="store_true", help="Enable Flask debug mode")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    app = create_app()
    python_exe = Path(sys.executable).resolve()
    expected_venv_python = Path.cwd() / "venv" / "Scripts" / "python.exe"
    app.logger.info("Python executable: %s", python_exe)
    if os.getenv("WHISPER_DEVICE", "cuda").strip().lower() == "cuda" and expected_venv_python.exists():
        try:
            if python_exe != expected_venv_python.resolve():
                app.logger.warning(
                    "WHISPER_DEVICE=cuda but server is not running with project venv Python. "
                    "Use: .\\venv\\Scripts\\python.exe .\\server.py"
                )
        except OSError:
            pass
    app.run(host=args.host, port=args.port, debug=args.debug)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
