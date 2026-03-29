#!/usr/bin/env python3
"""Build/append result.json entries from normalized transcripts.

Flow:
- Load existing result.json if present, else expected_result.json, else empty skeleton.
- If .result_build_state.json is missing/corrupt, initialize it from current
    normalized files (safe baseline; no accidental historical backfill).
- For each normalized_transcript/*.normalized.txt:
  - Parse outcome from first line: outcome=<label>
  - Parse [AGENT]/[USER] lines into transcript turns
  - Build transcript_text as one-line joined turns
  - Load responses from matching qa_json file (if found)
  - Load call duration from matching recordings/*.mp3 (mutagen if available)
  - direction is always outbound
- Append only entries whose transcript_text is not already present.
- Update total_samples and write result.json
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
EXPECTED_FILE = ROOT / "expected_result.json"
RESULT_FILE = ROOT / "result.json"
NORMALIZED_DIR = ROOT / "normalized_transcript"
QA_DIR = ROOT / "qa_json"
RECORDINGS_DIR = ROOT / "recordings"
STATE_FILE = ROOT / ".result_build_state.json"

OUTCOME_RE = re.compile(r"^outcome\s*=\s*([a-z_]+)\s*$", re.IGNORECASE)
TAG_RE = re.compile(r"^\[(AGENT|USER)\]\s*:\s*(.*)$", re.IGNORECASE)
CALL_KEY_RE = re.compile(r"(CA[A-Za-z0-9]+_RE[A-Za-z0-9]+)")


def load_base() -> dict[str, Any]:
    if RESULT_FILE.exists():
        try:
            raw = RESULT_FILE.read_text(encoding="utf-8").strip()
            if raw:
                data = json.loads(raw)
                if isinstance(data, dict):
                    return data
        except Exception:
            pass

    if EXPECTED_FILE.exists():
        try:
            raw = EXPECTED_FILE.read_text(encoding="utf-8").strip()
            if raw:
                data = json.loads(raw)
                if isinstance(data, dict):
                    return data
        except Exception:
            pass

    return {"total_samples": 0, "transcripts": []}


def load_processed_state() -> tuple[set[str], bool]:
    if not STATE_FILE.exists():
        return set(), False
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        items = data.get("processed_normalized_files", [])
        if isinstance(items, list):
            return {str(x) for x in items}, True
    except Exception:
        pass
    return set(), False


def save_processed_state(processed_files: set[str]) -> None:
    payload = {
        "processed_normalized_files": sorted(processed_files),
    }
    STATE_FILE.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def extract_call_key(name: str) -> str | None:
    m = CALL_KEY_RE.search(name)
    return m.group(1) if m else None


def parse_normalized_file(path: Path) -> tuple[str, list[dict[str, str]], str]:
    lines = [ln.rstrip() for ln in path.read_text(encoding="utf-8", errors="ignore").splitlines() if ln.strip()]
    outcome = "incomplete"
    turns: list[dict[str, str]] = []

    if lines:
        m = OUTCOME_RE.match(lines[0].strip())
        if m:
            outcome = m.group(1).lower()
            lines = lines[1:]

    transcript_text_parts: list[str] = []
    for ln in lines:
        m = TAG_RE.match(ln.strip())
        if not m:
            continue
        role = m.group(1).lower()
        message = m.group(2).strip()
        turns.append({"role": role, "message": message})
        transcript_text_parts.append(f"[{m.group(1).upper()}]: {message}")

    transcript_text = " ".join(transcript_text_parts)
    return outcome, turns, transcript_text


def find_matching_qa(normalized_file: Path) -> list[dict[str, Any]]:
    stem = normalized_file.stem  # typically <base>.normalized
    candidates = [
        QA_DIR / f"{stem}.qa.json",
        QA_DIR / f"{stem.replace('.normalized', '')}.qa.json",
    ]

    call_key = extract_call_key(normalized_file.name)
    if call_key:
        # fallback: pick newest qa file for this call key
        keyed = sorted(QA_DIR.glob(f"*{call_key}*.qa.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        if keyed:
            candidates.append(keyed[0])

    for candidate in candidates:
        if candidate.exists():
            try:
                data = json.loads(candidate.read_text(encoding="utf-8"))
                if isinstance(data, list):
                    return data
            except Exception:
                pass

    return []


def get_call_duration_seconds(normalized_file: Path) -> int:
    call_key = extract_call_key(normalized_file.name)
    if not call_key:
        print(f"[warn] no call key in normalized filename: {normalized_file.name}")
        return 0

    rec_candidates = sorted(RECORDINGS_DIR.glob(f"*{call_key}*.mp3"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not rec_candidates:
        print(f"[warn] no recording found for call key: {call_key}")
        return 0

    try:
        from mutagen.mp3 import MP3  # type: ignore

        selected = rec_candidates[0]
        length = MP3(str(selected)).info.length
        if not length:
            print(f"[warn] zero/empty MP3 length for: {selected}")
            return 0
        return int(round(float(length)))
    except Exception as exc:
        print(f"[warn] failed to read duration from MP3 for {rec_candidates[0].name}: {exc}")
        return 0


def main() -> int:
    base = load_base()
    transcripts = list(base.get("transcripts", []))
    processed_files, state_is_valid = load_processed_state()

    normalized_files = sorted(NORMALIZED_DIR.glob("*.normalized.txt"), key=lambda p: p.stat().st_mtime)

    # Deterministic first-run behavior for fresh clones:
    # if state is absent/corrupt, mark current files as already processed.
    if not state_is_valid:
        processed_files = {nf.name for nf in normalized_files}
        save_processed_state(processed_files)
        print(
            f"Initialized {STATE_FILE.name} with {len(processed_files)} existing normalized files "
            "(no backfill on this run)."
        )

    existing_text = {
        str(item.get("transcript_text", "")).strip()
        for item in transcripts
        if isinstance(item, dict)
    }

    appended = 0

    for nf in normalized_files:
        if nf.name in processed_files:
            continue

        outcome, turns, transcript_text = parse_normalized_file(nf)
        if not transcript_text:
            processed_files.add(nf.name)
            continue
        if transcript_text in existing_text:
            processed_files.add(nf.name)
            continue

        entry = {
            "outcome": outcome,
            "call_duration": get_call_duration_seconds(nf),
            "direction": "outbound",
            "transcript": turns,
            "transcript_text": transcript_text,
            "responses": find_matching_qa(nf),
        }
        transcripts.append(entry)
        existing_text.add(transcript_text)
        processed_files.add(nf.name)
        appended += 1

    base["transcripts"] = transcripts
    base["total_samples"] = len(transcripts)

    RESULT_FILE.write_text(json.dumps(base, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    save_processed_state(processed_files)
    print(f"Appended {appended} new entries to {RESULT_FILE}")
    print(f"total_samples={base['total_samples']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
