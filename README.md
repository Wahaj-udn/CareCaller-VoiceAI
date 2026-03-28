# Carecaller - Twilio ↔ Gemini Live Starter

This project now supports a real phone call path where Twilio media is streamed
to Gemini Live and Gemini audio is streamed back to the caller.

## Git-safe setup
- Copy `.env.example` to `.env` and fill in real values.
- `.env` is ignored by git via `.gitignore`.

## Files
- `call.py`: place outbound calls from your Twilio number
- `server.py`: Twilio webhook server that returns TwiML (`/voice/outbound`, `/voice/incoming`)
- `gemini_bridge.py`: WebSocket bridge between Twilio Media Streams and Gemini Live API
- `test_server.py`: unit tests for webhook XML responses
- `test_gemini_bridge.py`: unit tests for audio conversion helpers
- `requirements.txt`: Python dependencies

## Required environment variables
- `TWILIO_ACCOUNT_SID`
- `TWILIO_AUTH_TOKEN`
- `GEMINI_API_KEY`

## Optional environment variables
- `MEDIA_STREAM_URL` (required for calls, points to your bridge websocket)
- `GEMINI_MODEL` (default: `gemini-3.1-flash-live-preview`)
- `MISSION_PROMPT` (initial mission/system prompt for Gemini)
- `MISSION_PROMPT_FILE` (path to mission prompt text file)
- `MISSION_KICKOFF` (first text instruction to trigger Gemini greeting)
- `CALL_TO_NUMBER` (default destination for `call.py`)
- `CALL_FROM_NUMBER` (default Twilio source number for `call.py`)
- `OUTBOUND_TWIML_URL` (default TwiML URL for `call.py`)
- `RECORD_CALLS` (default `true`)
- `RECORDING_STATUS_CALLBACK_URL` (example: `https://<webhook-host>/voice/recording`)
- `RECORDINGS_DIR` (default: `recordings`)
- `AUTO_TRANSCRIBE_RECORDINGS` (default: `true`)
- `WHISPER_TRANSCRIPT_DIR` (default: `whisper_transcript`)
- `WHISPER_MODEL` (default: `small`)
- `WHISPER_DEVICE` (default: `cuda`)
- `WHISPER_COMPUTE_TYPE` (default: `float16`)
- `WHISPER_LANGUAGE` (default: `en`)
- `WHISPER_FALLBACK_TO_CPU` (default: `false`, keep `false` for strict CUDA-only)

## Run flow
1. Start `gemini_bridge.py` (WebSocket server).
2. Expose it publicly over **wss://**.
3. Set `MEDIA_STREAM_URL` to that public websocket URL.
4. Start `server.py` (Twilio webhook/TwiML server).
5. Expose webhook server publicly over HTTPS.
6. Run `call.py` with `--twiml-url https://<public-webhook>/voice/outbound`.

`call.py` is `.env`-first: if `CALL_TO_NUMBER`, `CALL_FROM_NUMBER`, and
`OUTBOUND_TWIML_URL` are set, you can run it without those flags.

## Webhook endpoints in `server.py`
- `GET /health`
- `POST /voice/incoming`
- `POST /voice/outbound`
- `POST /voice/events`
- `POST /voice/recording` (Twilio recording callback; downloads MP3 locally)

## Call recording download (MP3)
- `call.py` now requests Twilio recording by default (`RECORD_CALLS=true`).
- When Twilio sends `RecordingStatus=completed` to `/voice/recording`, `server.py` downloads
	`RecordingUrl + .mp3` with Twilio HTTP Basic auth.
- Saved file format:
	- `recordings/<UTC_TIMESTAMP>_<CallSid>_<RecordingSid>.mp3`
- If a filename already exists, download is skipped.

## Automatic Faster-Whisper transcription
- As soon as a new recording is saved in `recordings/`, `server.py` starts a background
	transcription job (when `AUTO_TRANSCRIBE_RECORDINGS=true`).
- Transcript file format:
	- `whisper_transcript/<recording_file_stem>.txt`
- The transcript includes language/duration metadata and timestamped text segments.
- On Windows, CUDA DLL folders from NVIDIA pip packages are auto-registered; with
	`WHISPER_FALLBACK_TO_CPU=false` (default), transcription fails fast if CUDA runtime is unavailable.

You can also manually transcribe the newest recording:
- `python whisper_transcriber.py --recordings-dir recordings --transcript-dir whisper_transcript --device cuda --compute-type float16`

## Bridge behavior in `gemini_bridge.py`
- Receives Twilio μ-law 8k audio
- Converts to PCM16 16k and sends to Gemini Live
- Receives Gemini audio and converts it back to Twilio μ-law 8k
- Handles basic interruption by clearing buffered model audio when caller speech is detected
