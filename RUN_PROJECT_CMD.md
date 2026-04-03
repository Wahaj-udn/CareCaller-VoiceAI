# Carecaller Runbook (Windows CMD)

This guide gives the **exact CMD commands** and the exact order to run the project.

> Shell target: **Command Prompt (cmd.exe)**

---

## 1) Prerequisites

- Windows machine
- Python 3.11+
- Twilio account + verified phone numbers
- Internet tunnel tools installed:
  - `cloudflared` (for bridge public URL)
  - `ngrok` (for webhook public URL)

---

## 2) One-time setup

Open **CMD** and run:

```cmd
cd /d d:\Wahaj\Projects\Hackathons\Carecaller\ps2-2
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```

Create `.env` from template:

```cmd
copy .env.example .env
```

Then edit `.env` and fill at least:
- `TWILIO_ACCOUNT_SID`
- `TWILIO_AUTH_TOKEN`
- `GEMINI_API_KEY`
- `CALL_TO_NUMBER`
- `CALL_FROM_NUMBER`

---

## 3) Run sequence (exact order)

Use **4 separate CMD windows**.

## Terminal 1 — Start Gemini bridge

```cmd
cd /d d:\Wahaj\Projects\Hackathons\Carecaller\ps2-2
venv\Scripts\activate
python gemini_bridge.py --host 0.0.0.0 --port 8765
```

Keep this window running.

---

## Terminal 2 — Expose bridge publicly (cloudflared)

```cmd
cd /d d:\Wahaj\Projects\Hackathons\Carecaller\ps2-2
cloudflared tunnel --url http://localhost:8765
```

You will get a public URL like:
- `https://xxxx.trycloudflare.com`

Set in `.env`:
- `MEDIA_STREAM_URL=wss://xxxx.trycloudflare.com/media-stream`

> Important: Use `wss://` and include `/media-stream` path.

---

## Terminal 3 — Start webhook server

```cmd
cd /d d:\Wahaj\Projects\Hackathons\Carecaller\ps2-2
venv\Scripts\activate
python server.py
```

Keep this window running.

---

## Terminal 4 — Expose webhook publicly (ngrok)

```cmd
ngrok http 5000
```

You will get a public URL like:
- `https://abcd-1234.ngrok-free.app`

Set in `.env`:
- `WEBHOOK_BASE_URL=https://abcd-1234.ngrok-free.app`
- `OUTBOUND_TWIML_URL=https://abcd-1234.ngrok-free.app/voice/outbound`
- `RECORDING_STATUS_CALLBACK_URL=https://abcd-1234.ngrok-free.app/voice/recording`

---

## 4) Place a test call

Open another CMD window:

```cmd
cd /d d:\Wahaj\Projects\Hackathons\Carecaller\ps2-2
venv\Scripts\activate
python call.py
```

If `.env` has call numbers/URLs set, no extra flags are needed.

### CSV queue calling (row-by-row)

If you want to call multiple patients from `patient_checkin.csv` using `Patient Name` + `Phone Number` columns:

```cmd
cd /d d:\Wahaj\Projects\Hackathons\Carecaller\ps2-2
venv\Scripts\activate
python call_csv.py --csv-file patient_checkin.csv
```

Notes:
- Progress is stored in `.call_csv_state.json`.
- Re-running continues from next row.
- Dry run preview:

```cmd
python call_csv.py --csv-file patient_checkin.csv --dry-run
```

---

## 5) Expected outputs after a successful call

You should see files appear in this order:

1. `recordings\*.mp3`
2. `whisper_transcript\*.txt`
3. `final_transcript\*.txt`
4. `normalized_transcript\*.normalized.txt`
5. `qa_json\*.qa.json`
6. `result.json` updated

---

## 6) Optional manual commands

## Rebuild/append result dataset manually

```cmd
cd /d d:\Wahaj\Projects\Hackathons\Carecaller\ps2-2
venv\Scripts\activate
python build_result_json.py
```

## Run normalization manually for one final transcript

```cmd
cd /d d:\Wahaj\Projects\Hackathons\Carecaller\ps2-2
venv\Scripts\activate
python normalize_transcript_with_gemini.py --input-file final_transcript\YOUR_FILE.txt
```

---

## 7) Fast troubleshooting

- **Twilio 401/20003**
  - Verify `TWILIO_ACCOUNT_SID` and `TWILIO_AUTH_TOKEN` in `.env`
  - Re-run `python call.py`

- **No bridge connection**
  - Check `MEDIA_STREAM_URL` is `wss://.../media-stream`
  - Ensure `gemini_bridge.py` is running

- **No recording callback**
  - Check `RECORDING_STATUS_CALLBACK_URL`
  - Ensure `server.py` is running and reachable via ngrok

- **No `result.json` updates**
  - Confirm `AUTO_UPDATE_RESULT_JSON=true` in `.env`
  - Confirm a new normalized file was generated

---

## 8) Stop all services

In each CMD window running a server/tunnel, press:

```cmd
Ctrl + C
```
