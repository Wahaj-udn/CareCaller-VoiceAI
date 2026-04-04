#!/usr/bin/env python3
"""Build output.csv from patient_checkin.csv + qa_json call outputs.

Strategy:
- Keep patient_checkin.csv headers and row order.
- Map QA files to patients by extracting CallSid from QA filename,
  then reading conversation/<timestamp>_<CallSid>.txt for # patient_name=...
- For each patient, use the latest QA file (by QA filename timestamp).
- Fill health-answer columns from canonical QA questions.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable


QUESTION_TO_COLUMN = {
    "howhaveyoubeenfeelingoverall": "Overall Feeling",
    "whatsyourcurrentweightinpounds": "Weight (lbs)",
    "whatsyourheightinfeetandinches": "Height (ft/in)",
    "howmuchweighthaveyoulostthispastmonthinpounds": "Weight Lost This Month (lbs)",
    "anysideeffectsfromyourmedicationthismonth": "Medication Side Effects",
    "areyousatisfiedwithyourrateofweightloss": "Satisfied with Weight Loss Rate",
    "whatsyourgoalweightinpounds": "Goal Weight (lbs)",
    "anyrequestsaboutyourdosage": "Dosage Requests",
    "haveyoustartedanynewmedicationsorsupplementssincelastmonth": "New Medications/Supplements",
    "doyouhaveanynewmedicalconditionssinceyourlastcheckin": "New Medical Conditions",
    "anynewallergies": "New Allergies",
    "anysurgeriessinceyourlastcheckin": "Recent Surgeries",
    "anyquestionsforyourdoctor": "Questions for Doctor",
    "hasyourshippingaddresschanged": "Shipping Address Changed",
}


@dataclass
class QaMatch:
    qa_path: Path
    patient_name: str
    call_sid: str
    timestamp: str


@dataclass
class CallMeta:
    outcome: str
    direction: str
    call_duration: str
    transcript: str


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (text or "").strip().lower())


def _extract_call_sid_and_ts(qa_filename: str) -> tuple[str | None, str | None]:
    # Example: 20260403T175510Z_CA..._RE....qa.json
    m = re.match(r"^(\d{8}T\d{6}Z)_(CA[a-zA-Z0-9]+)_", qa_filename)
    if not m:
        return None, None
    return m.group(2), m.group(1)


def _format_call_time(ts: str | None) -> str:
    if not ts:
        return ""
    try:
        dt = datetime.strptime(ts, "%Y%m%dT%H%M%SZ")
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return ts


def _conversation_file_for_call(conversation_dir: Path, call_sid: str) -> Path | None:
    matches = sorted(conversation_dir.glob(f"*_{call_sid}.txt"))
    if not matches:
        return None
    return matches[-1]


def _extract_patient_name_from_conversation(path: Path) -> str | None:
    try:
        for line in path.read_text(encoding="utf-8", errors="ignore").splitlines()[:10]:
            if line.startswith("# patient_name="):
                return line.split("=", 1)[1].strip()
    except Exception:
        return None
    return None


def _iter_qa_matches(qa_dir: Path, conversation_dir: Path) -> Iterable[QaMatch]:
    for qa_path in sorted(qa_dir.glob("*.qa.json")):
        if qa_path.name.endswith(".normalized.qa.json"):
            continue
        call_sid, ts = _extract_call_sid_and_ts(qa_path.name)
        if not call_sid or not ts:
            continue
        convo = _conversation_file_for_call(conversation_dir, call_sid)
        if not convo:
            continue
        patient_name = _extract_patient_name_from_conversation(convo)
        if not patient_name:
            continue
        yield QaMatch(qa_path=qa_path, patient_name=patient_name, call_sid=call_sid, timestamp=ts)


def _load_qa_answers(qa_path: Path) -> dict[str, str]:
    try:
        payload = json.loads(qa_path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(payload, list):
        return {}

    out: dict[str, str] = {}
    for item in payload:
        if not isinstance(item, dict):
            continue
        q = _norm(str(item.get("question", "")))
        a = str(item.get("answer", "")).strip()
        column = QUESTION_TO_COLUMN.get(q)
        if column is not None:
            out[column] = a
    return out


def _extract_patient_name_from_result_record(record: dict) -> str | None:
    transcript_text = str(record.get("transcript_text", "") or "")
    if transcript_text:
        patterns = [
            r"is this\s+([^?.!,]+)",
            r"speaking with\s+([^?.!,]+)",
            r"trying to reach\s+([^?.!,]+)",
        ]
        for pattern in patterns:
            m = re.search(pattern, transcript_text, re.IGNORECASE)
            if m:
                name = m.group(1).strip(" .,!?")
                # Strip common titles
                name = re.sub(r"^(mr\.?|mrs\.?|ms\.?|miss|dr\.?)\s+", "", name, flags=re.IGNORECASE)
                return name.strip()

    transcript_items = record.get("transcript")
    if isinstance(transcript_items, list):
        for item in transcript_items[:2]:
            if not isinstance(item, dict):
                continue
            message = str(item.get("message", "") or "")
            m = re.search(r"is this\s+([^?.!,]+)", message, re.IGNORECASE)
            if m:
                return m.group(1).strip(" .,!?")
    return None


def _record_transcript_text(record: dict) -> str:
    text = str(record.get("transcript_text", "") or "").strip()
    if text:
        return text
    transcript_items = record.get("transcript")
    if not isinstance(transcript_items, list):
        return ""
    parts: list[str] = []
    for item in transcript_items:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role", "") or "").strip().upper() or "UNKNOWN"
        message = str(item.get("message", "") or "").strip()
        if not message:
            continue
        parts.append(f"[{role}]: {message}")
    return " ".join(parts)


def _load_result_meta_by_patient(result_json: Path) -> dict[str, CallMeta]:
    if not result_json.exists():
        return {}
    try:
        payload = json.loads(result_json.read_text(encoding="utf-8"))
    except Exception:
        return {}

    transcripts = payload.get("transcripts") if isinstance(payload, dict) else None
    if not isinstance(transcripts, list):
        return {}

    latest: dict[str, CallMeta] = {}
    for record in transcripts:
        if not isinstance(record, dict):
            continue
        name = _extract_patient_name_from_result_record(record)
        if not name:
            continue
        key = _norm(name)
        latest[key] = CallMeta(
            outcome=str(record.get("outcome", "") or "").strip(),
            direction=str(record.get("direction", "") or "").strip(),
            call_duration=str(record.get("call_duration", "") or "").strip(),
            transcript=_record_transcript_text(record),
        )
    return latest


def build_output_csv(
    input_csv: Path,
    qa_dir: Path,
    conversation_dir: Path,
    output_csv: Path,
    result_json: Path | None = None,
) -> dict[str, int]:
    with input_csv.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        headers = reader.fieldnames or []
        rows = list(reader)

    if not headers:
        raise RuntimeError(f"No headers found in {input_csv}")

    patient_col = "Patient Name"
    if patient_col not in headers:
        raise RuntimeError("Input CSV is missing 'Patient Name' column.")

    required_fields = ["Call Time", "Call Duration", "Direction", "Outcome", "transcript"]
    for field in required_fields:
        if field not in headers:
            headers.append(field)

    if result_json is None:
        result_json = input_csv.parent / "result.json"
    result_meta_by_patient = _load_result_meta_by_patient(result_json)

    latest_by_patient: dict[str, QaMatch] = {}
    total_qa_considered = 0
    for match in _iter_qa_matches(qa_dir, conversation_dir):
        total_qa_considered += 1
        key = _norm(match.patient_name)
        prev = latest_by_patient.get(key)
        if prev is None or match.timestamp > prev.timestamp:
            latest_by_patient[key] = match

    matched_rows = 0
    for row in rows:
        p_name = (row.get(patient_col) or "").strip()
        key = _norm(p_name)
        match = latest_by_patient.get(key)
        if not match:
            continue

        answers = _load_qa_answers(match.qa_path)
        for col, val in answers.items():
            if col in headers:
                row[col] = val

        row["Call Time"] = _format_call_time(match.timestamp)

        meta = result_meta_by_patient.get(key)
        if meta:
            if meta.call_duration:
                row["Call Duration"] = meta.call_duration
            if meta.direction:
                row["Direction"] = meta.direction
            if meta.outcome:
                row["Outcome"] = meta.outcome
            if meta.transcript:
                row["transcript"] = meta.transcript

        matched_rows += 1

    with output_csv.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=headers)
        writer.writeheader()
        writer.writerows(rows)

    return {
        "rows": len(rows),
        "qa_considered": total_qa_considered,
        "patient_matches": len(latest_by_patient),
        "rows_filled": matched_rows,
    }


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Build output.csv from patient_checkin.csv and qa_json outputs")
    p.add_argument("--input-csv", default="patient_checkin.csv")
    p.add_argument("--qa-dir", default="qa_json")
    p.add_argument("--conversation-dir", default="conversation")
    p.add_argument("--output-csv", default="output.csv")
    p.add_argument("--result-json", default="result.json")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    summary = build_output_csv(
        input_csv=Path(args.input_csv),
        qa_dir=Path(args.qa_dir),
        conversation_dir=Path(args.conversation_dir),
        output_csv=Path(args.output_csv),
        result_json=Path(args.result_json),
    )
    print(
        "Built output CSV:",
        f"rows={summary['rows']}",
        f"qa_considered={summary['qa_considered']}",
        f"patient_matches={summary['patient_matches']}",
        f"rows_filled={summary['rows_filled']}",
        sep=" ",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
