# Carecaller Architecture and Setup Guide

## Purpose

This document explains:

1. System architecture (what each component does)
2. Data flow across the pipeline
3. Environment configuration
4. Local setup and run instructions
5. Troubleshooting checklist

It is written to be practical for new developers and operators.

---

## 1) High-level architecture

Carecaller is a Python-based voice pipeline integrating Twilio, Gemini Live, Whisper transcription, and structured post-processing.

### Core runtime components

- `call.py`
  - Initiates outbound call via Twilio REST API.

- `server.py`
  - Twilio webhook server (Flask).
  - Returns TwiML for incoming/outbound routes.
  - Handles recording callbacks and triggers downstream jobs.

- `gemini_bridge.py`
  - WebSocket bridge between Twilio media stream and Gemini Live.
  - Converts audio formats in both directions.
  - Writes per-call conversation logs.

### Post-call processing components

- `whisper_transcriber.py`
  - Transcribes MP3 into timestamped text.

- `final_transcript_builder.py`
  - Aligns Whisper output with conversation logs.
  - Produces speaker-labeled final transcript.

- `normalize_transcript_with_gemini.py`
  - Cleans and normalizes transcript into strict `[AGENT]/[USER]` format.
  - Enforces first line `outcome=<label>`.

- `extract_responses.py`
  - Extracts answers for 14 canonical questions into JSON.

- `build_result_json.py`
  - Builds/appends aggregate dataset in `result.json`.
  - Reads normalized transcript + QA JSON + recording duration.
  - Uses `.result_build_state.json` for incremental appends.

---

## 2) Folder-level data flow

## 2.1 Artifacts by stage

- `recordings/`
  - Downloaded call MP3 files.

- `conversation/`
  - Per-call raw conversation logs from live bridge.

- `whisper_transcript/`
  - ASR transcripts from recordings.

- `final_transcript/`
  - Speaker-attributed transcript after alignment.

- `normalized_transcript/`
  - Clean deterministic dialogue with `outcome=` first line.

- `qa_json/`
  - Canonical question-answer pairs.

- `result.json`
  - Aggregated final dataset.

- `.result_build_state.json`
  - Local state of processed normalized files (incremental mode).

## 2.2 Sequence

1. Twilio call connected
2. Live audio streamed to Gemini via bridge
3. Recording completed callback sent to `server.py`
4. MP3 downloaded to `recordings/`
5. Transcription to `whisper_transcript/`
6. Final transcript built in `final_transcript/`
7. Normalized transcript in `normalized_transcript/`
8. QA extraction to `qa_json/`
9. Aggregation append/update in `result.json`

---

## 3) Configuration model

Environment variables are loaded from `.env` (template: `.env.example`).

## 3.1 Required

- `TWILIO_ACCOUNT_SID`
- `TWILIO_AUTH_TOKEN`
- `GEMINI_API_KEY`

## 3.2 Key runtime URLs

- `MEDIA_STREAM_URL` -> public WSS URL to `gemini_bridge.py`
- `WEBHOOK_BASE_URL` -> public HTTPS URL to `server.py`
- `OUTBOUND_TWIML_URL` -> typically `${WEBHOOK_BASE_URL}/voice/outbound`
- `RECORDING_STATUS_CALLBACK_URL` -> typically `${WEBHOOK_BASE_URL}/voice/recording`

## 3.3 Automation toggles

- `AUTO_TRANSCRIBE_RECORDINGS`
- `AUTO_BUILD_FINAL_TRANSCRIPT`
- `AUTO_NORMALIZE_TRANSCRIPT`
- `AUTO_SAVE_QA_JSON`
- `AUTO_UPDATE_RESULT_JSON`

## 3.4 Normalization/aggregation knobs

- `TRANSCRIPT_NORMALIZER_MODEL`
- `TRANSCRIPT_NORMALIZER_GEMINI_API_KEY` (optional dedicated key)
- `NORMALIZED_TRANSCRIPT_DIR`
- `QA_JSON_DIR`

---

## 4) Local setup (Windows / PowerShell)

## 4.1 Prerequisites

- Python 3.11+
- Twilio account + number
- Public tunnel tools (for HTTPS/WSS), e.g., ngrok/cloudflared
- Optional GPU setup for faster-whisper CUDA mode

## 4.2 Install dependencies

```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## 4.3 Configure environment

1. Copy `.env.example` to `.env`
2. Fill required secrets and public URLs
3. Set automation flags as needed

> Security: never commit real secrets from `.env`.

---

## 5) Running the system

## 5.1 Start bridge

```powershell
.\venv\Scripts\python.exe .\gemini_bridge.py
```

Expose bridge publicly over **WSS** and update `MEDIA_STREAM_URL`.

## 5.2 Start webhook server

```powershell
.\venv\Scripts\python.exe .\server.py
```

Expose server publicly over **HTTPS** and update:
- `WEBHOOK_BASE_URL`
- `OUTBOUND_TWIML_URL`
- `RECORDING_STATUS_CALLBACK_URL`

## 5.3 Place outbound call

```powershell
.\venv\Scripts\python.exe .\call.py
```

(Uses `.env` defaults for numbers and TwiML URL.)

---

## 6) First-run and incremental behavior for result building

`build_result_json.py` supports safe incremental updates:

- If `.result_build_state.json` exists and is valid:
  - only new normalized files are appended.

- If state file is missing/corrupt:
  - state initializes from currently existing normalized files,
  - no historical backfill on that run,
  - future runs append new files only.

- If `result.json` is missing/empty/invalid:
  - builder safely falls back to `expected_result.json` or empty skeleton.

---

## 7) Testing and verification

## 7.1 Syntax check (quick)

```powershell
.\venv\Scripts\python.exe -m py_compile call.py server.py build_result_json.py normalize_transcript_with_gemini.py
```

## 7.2 Unit tests (example)

```powershell
.\venv\Scripts\python.exe -m unittest -q test_normalize_transcript.py
```

(Other test files can be run similarly.)

---

## 8) Troubleshooting guide

## 8.1 Twilio auth error 401 / code 20003

- Verify SID/token pair in `.env`
- Ensure correct account/subaccount pairing
- Rotate token if needed
- `call.py` uses `.env` override to avoid stale shell vars

## 8.2 No recording downloaded

- Check `RECORD_CALLS=true`
- Verify `RECORDING_STATUS_CALLBACK_URL` public and reachable
- Confirm Twilio credentials in `server.py` environment

## 8.3 No transcription generated

- Check `AUTO_TRANSCRIBE_RECORDINGS=true`
- Validate Whisper device settings (`WHISPER_DEVICE`, CUDA availability)

## 8.4 Wrong/missing `result.json` updates

- Ensure `AUTO_UPDATE_RESULT_JSON=true`
- Check `normalized_transcript/` has `.normalized.txt` files
- Inspect `.result_build_state.json` behavior for first-run/incremental mode

## 8.5 QA alignment issues

- Inspect normalized transcript for merged questions or correction flow
- Re-run normalization with strict canonical wording rules

---

## 9) Suggested operational practices

1. Keep `.env` local and rotate secrets regularly.
2. Keep runtime artifacts ignored unless intentionally versioned.
3. Add small regression tests whenever prompt rules change.
4. For production hardening, add structured logging + alerting around callback failures.

---

## 10) Reference map

- Runtime entry points: `call.py`, `server.py`, `gemini_bridge.py`
- Processing chain: `whisper_transcriber.py` -> `final_transcript_builder.py` -> `normalize_transcript_with_gemini.py` -> `extract_responses.py` -> `build_result_json.py`
- Docs overview: `README.md`
- Incident history: `PROJECT_RETROSPECTIVE.md`
