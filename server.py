#!/usr/bin/env python3
"""Twilio voice webhook server for Carecaller.

Endpoints:
- GET /health
- POST /voice/incoming
- POST /voice/outbound
- POST /voice/events

Environment variables:
- PORT (default: 5000)
- HOST (default: 0.0.0.0)
- MEDIA_STREAM_URL (optional): wss://... endpoint that receives Twilio media stream
"""

from __future__ import annotations

import argparse
import os

from dotenv import load_dotenv
from flask import Flask, Response, jsonify, request
from twilio.twiml.voice_response import Connect, VoiceResponse

load_dotenv()


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
    app.run(host=args.host, port=args.port, debug=args.debug)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
