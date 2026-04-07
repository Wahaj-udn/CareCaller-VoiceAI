# Carecaller – AI Voice Healthcare Assistant

(The LAST response in the [result.json](./result.json#L2445) file corresponds to the attached demo conversation submitted on unstop i.e [CareCaller_Demo.mp4](./CareCaller_Demo.mp4))

Carecaller is an end-to-end AI voice system that automates healthcare check-in calls using real-time conversation and structured data extraction.

It combines telephony, real-time AI, and post-call processing into a single pipeline that can both talk like a human and produce clean, usable data.

Most healthcare call systems either: sound robotic, or generate messy, unusable data
This project aims to bridges that gap:
Natural conversations in → clean structured insights out

<img width="1901" height="1199" alt="image" src="https://github.com/user-attachments/assets/4c58ad3e-3917-400a-a328-92a0caa42597" />
<img width="1900" height="1199" alt="image" src="https://github.com/user-attachments/assets/05b5d7c4-af7d-4127-8318-dcc471a50964" />
<img width="1899" height="1199" alt="image" src="https://github.com/user-attachments/assets/48b6d15b-e0ca-4c5d-826f-8f20895fa19d" />
<img width="1901" height="1199" alt="image" src="https://github.com/user-attachments/assets/90ec9536-316c-4f2f-b48d-4efe83bbde6e" />

## High-level overview

### Purpose
Carecaller automates healthcare check-in calls end to end:
- run a live AI voice conversation,
- record and transcribe the call,
- normalize and extract structured answers,
- produce dataset-ready JSON outputs for downstream analysis.

It is designed to be both conversational for users and deterministic for data pipelines.

### End-to-end workflow
1. A call is placed/received through Twilio.
2. Live audio is streamed to Gemini through the bridge.
3. Twilio recording callback triggers MP3 download.
4. MP3 is transcribed with Whisper.
5. Transcript is aligned with conversation logs for speaker fidelity.
6. Transcript is normalized to strict `[AGENT]/[USER]` format with `outcome=<label>`.
7. Canonical Q&A is extracted for 14 fixed questions.
8. Aggregated sample is appended into `result.json`.

### System at a glance
- **Call layer:** Twilio call routing, webhooks, recordings.
- **Realtime AI layer:** `gemini_bridge.py` + Gemini Live for bidirectional voice.
- **Post-call NLP layer:** Whisper transcription, transcript alignment, normalization.
- **Structured data layer:** Q&A extraction + `result.json` builder with incremental state.

### Main outputs
- `conversation/*.txt` -> per-call live logs
- `whisper_transcript/*.txt` -> ASR transcript
- `final_transcript/*.txt` -> speaker-labeled transcript
- `normalized_transcript/*.normalized.txt` -> deterministic cleaned transcript
- `qa_json/*.qa.json` -> 14-question extracted answers
- `result.json` -> aggregated final dataset
- `output.csv` -> patient_checkin schema populated with latest mapped QA answers

## Additional documentation
- `PROJECT_RETROSPECTIVE.md`: detailed project journey, issues faced, and solutions.
- `ARCHITECTURE_AND_SETUP.md`: architecture, end-to-end flow, setup, runbook, and troubleshooting.
- `ARCHITECTURE_DIAGRAM_AND_FLOW.md`: visual architecture diagram plus very detailed stage-by-stage flow.
- `RUN_PROJECT_CMD.md`: exact Windows CMD commands and step-by-step run sequence.

## Technical reference

## Git-safe setup
- Copy `.env.example` to `.env` and fill in real values.
- `.env` is ignored by git via `.gitignore`.

## Files
- `call.py`: place outbound calls from your Twilio number
- `call_csv.py`: initialize/resume CSV queue and place only the first pending call
- `csv_call_queue.py`: callback-driven queue engine (next call starts only after terminal callback)
- `server.py`: Twilio webhook server that returns TwiML (`/voice/outbound`, `/voice/incoming`)
- `gemini_bridge.py`: WebSocket bridge between Twilio Media Streams and Gemini Live API
- `test_server.py`: unit tests for webhook XML responses
- `test_gemini_bridge.py`: unit tests for audio conversion helpers
- `requirements.txt`: Python dependencies

## Required environment variables
- `TWILIO_ACCOUNT_SID`
- `TWILIO_AUTH_TOKEN`
- `GEMINI_API_KEY`

## Other environment variables
- `MEDIA_STREAM_URL` (required for calls, points to your bridge websocket)
- `GEMINI_MODEL` (default: `gemini-3.1-flash-live-preview`)
- `MISSION_PROMPT` (initial mission/system prompt for Gemini)
- `MISSION_PROMPT_FILE` (path to mission prompt text file)
- `MISSION_KICKOFF` (first text instruction to trigger Gemini greeting)
- `CALL_TO_NUMBER` (default destination for `call.py`)
- `CALL_FROM_NUMBER` (default Twilio source number for `call.py`)
- `OUTBOUND_TWIML_URL` (default TwiML URL for `call.py`)
- `RECORD_CALLS` (default `true`)
- `CALL_CSV_PATH` (default: `patient_checkin.csv`, used by `call_csv.py`)
- `CALL_CSV_NAME_COLUMN` (optional explicit name column header)
- `CALL_CSV_PHONE_COLUMN` (optional explicit phone column header)
- `CALL_CSV_START_INDEX` (default: `0`, 0-based fallback start row)
- `CALL_CSV_DRY_RUN` (default: `false`)
- `CALL_CSV_RESUME` (default: `false`, resume existing queue state)
- `CALL_CSV_STATE_FILE` (default: `.call_csv_state.json`)
- `RECORDING_STATUS_CALLBACK_URL` (example: `https://<webhook-host>/voice/recording`)
- `RECORDINGS_DIR` (default: `recordings`)
- `AUTO_TRANSCRIBE_RECORDINGS` (default: `true`)
- `WHISPER_TRANSCRIPT_DIR` (default: `whisper_transcript`)
- `WHISPER_MODEL` (default: `small`)
- `WHISPER_DEVICE` (default: `cuda`)
- `WHISPER_COMPUTE_TYPE` (default: `float16`)
- `WHISPER_LANGUAGE` (default: `en`)
- `WHISPER_FALLBACK_TO_CPU` (default: `false`, keep `false` for strict CUDA-only)
- `AUTO_BUILD_FINAL_TRANSCRIPT` (default: `true`)
- `FINAL_TRANSCRIPT_DIR` (default: `final_transcript`)
- `AUTO_NORMALIZE_TRANSCRIPT` (default: `true`, runs Gemini normalizer on final transcripts)
- `AUTO_SAVE_QA_JSON` (default: `true`, extracts canonical Q&A JSON from normalized transcript)
- `QA_JSON_DIR` (default: `qa_json`)
- `AUTO_UPDATE_RESULT_JSON` (default: `true`, appends/refreshes `result.json` after each processed call)
- `AUTO_UPDATE_OUTPUT_CSV` (default: `true`, refreshes `output.csv` after each processed call)
- `INPUT_PATIENT_CSV` (default: `patient_checkin.csv`, source schema/rows for `output.csv`)
- `OUTPUT_CSV_FILE` (default: `output.csv`)
- `CONVERSATION_DIR` (default: `conversation`)
- `CONVERSATION_PER_CALL` (default: `true`, creates one transcript file per call)

## Run flow
1. Start `gemini_bridge.py` (WebSocket server).
2. Expose it publicly over **wss://**.
3. Set `MEDIA_STREAM_URL` to that public websocket URL.
4. Start `server.py` (Twilio webhook/TwiML server).
5. Expose webhook server publicly over HTTPS.
6. Run `call.py` with `--twiml-url https://<public-webhook>/voice/outbound`.

`call.py` is `.env`-first: if `CALL_TO_NUMBER`, `CALL_FROM_NUMBER`, and
`OUTBOUND_TWIML_URL` are set, you can run it without those flags.

CSV queue run example (strict one-by-one by completion callback):
- `python call_csv.py --csv-file patient_checkin.csv`
- script initializes queue state and places only the first call
- Twilio posts status callbacks to `/voice/events?csv_queue=1`
- when active call reaches terminal status (`completed`, `busy`, `failed`, `no-answer`, `canceled`), server places the next call
- queue progress is persisted in `.call_csv_state.json`

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

## Final transcript (speaker-labeled from comparison)
- After Whisper transcription completes, the server can automatically build a speaker-labeled
	final transcript by aligning `whisper_transcript/*.txt` with matching `conversation/*.txt`.
- Output format:
	- `final_transcript/<whisper_transcript_filename>.txt`
	- each line: `[start-end] agent|user> text`
- Matching is call-aware (`CallSid`) and uses ordered text similarity with agent timestamp boosts.

Manual run example:
- `python final_transcript_builder.py --whisper-file whisper_transcript/<file>.txt --conversation-dir conversation --output-dir final_transcript`

## Gemini transcript normalization (clean [AGENT]/[USER] only)
- Use `normalize_transcript_with_gemini.py` to pass a transcript file to Gemini with a strict
	normalization prompt and get cleaned dialogue-only output.
- API key resolution order:
	1) `TRANSCRIPT_NORMALIZER_GEMINI_API_KEY` (preferred, dedicated key)
	2) `GEMINI_API_KEY` (fallback)
- Output file:
	- `normalized_transcript/<input_stem>.normalized.txt` (default)

Manual run example:
- `python normalize_transcript_with_gemini.py --input-file final_transcript/<file>.txt`

When `AUTO_NORMALIZE_TRANSCRIPT=true`, `server.py` automatically normalizes each newly generated
final transcript into `normalized_transcript/`.

## Automatic Q&A JSON extraction
- After normalization, `server.py` can automatically extract answers for the 14 canonical questions
	and save structured JSON output.
- Output file format:
	- `qa_json/<final_transcript_stem>.qa.json`
- Controlled by:
	- `AUTO_SAVE_QA_JSON=true`
	- `QA_JSON_DIR=qa_json`

## Automatic result dataset update (`result.json`)
- After normalization (and optional Q&A extraction), the pipeline can auto-update `result.json`
	using available files from `normalized_transcript/`, `qa_json/`, and `recordings/`.
- Controlled by:
	- `AUTO_UPDATE_RESULT_JSON=true`
- First run behavior: if `.result_build_state.json` is absent/corrupt, the builder initializes state from currently existing `normalized_transcript/*.normalized.txt` files (no historical backfill on that run).

## Automatic patient output CSV update (`output.csv`)
- After each processed call, the pipeline can automatically regenerate `output.csv` from:
	- `patient_checkin.csv` (or `INPUT_PATIENT_CSV`)
	- latest mapped `qa_json/*.qa.json`
	- call metadata inferred from `conversation/*.txt`
- Controlled by:
	- `AUTO_UPDATE_OUTPUT_CSV=true`
	- `INPUT_PATIENT_CSV=patient_checkin.csv`
	- `OUTPUT_CSV_FILE=output.csv`

## Bridge behavior in `gemini_bridge.py`
- Receives Twilio μ-law 8k audio
- Converts to PCM16 16k and sends to Gemini Live
- Receives Gemini audio and converts it back to Twilio μ-law 8k
- Handles basic interruption by clearing buffered model audio when caller speech is detected
- Logs call transcripts as per-call files in `conversation/` (for example: `20260329T000000Z_CAxxxx.txt`)
