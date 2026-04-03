#!/usr/bin/env python3
"""Callback-driven CSV call queue utilities.

This module powers one-by-one dialing where the next call is started only
after Twilio reports a terminal state for the active call.
"""

from __future__ import annotations

import csv
import json
import os
import re
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from dotenv import load_dotenv
from twilio.base.exceptions import TwilioRestException
from twilio.rest import Client

load_dotenv(override=True)

TERMINAL_STATUSES = {"completed", "busy", "failed", "no-answer", "canceled"}


def _normalize_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (value or "").strip().lower())


def _is_truthy(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def _is_e164(value: str) -> bool:
    return bool(re.match(r"^\+[1-9]\d{7,14}$", (value or "").strip()))


def _append_query(url: str, params: dict[str, str]) -> str:
    parsed = urlparse(url)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query.update(params)
    return urlunparse(parsed._replace(query=urlencode(query)))


def _load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_state(path: Path, state: dict[str, Any]) -> None:
    path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")


def _detect_columns(headers: list[str], name_column: str | None, phone_column: str | None) -> tuple[str, str]:
    if not headers:
        raise ValueError("CSV has no headers.")

    header_lookup = {_normalize_key(h): h for h in headers}

    def resolve(explicit: str | None, candidates: list[str], label: str) -> str:
        if explicit:
            n = _normalize_key(explicit)
            if n in header_lookup:
                return header_lookup[n]
            raise ValueError(f"Configured {label} column not found: {explicit}")

        for c in candidates:
            n = _normalize_key(c)
            if n in header_lookup:
                return header_lookup[n]
        raise ValueError(f"Could not auto-detect {label} column. Available headers: {headers}")

    resolved_name = resolve(name_column, ["patient name", "name", "full name", "patient"], "name")
    resolved_phone = resolve(
        phone_column,
        ["phone number", "phone", "mobile", "mobile number", "to number"],
        "phone",
    )
    return resolved_name, resolved_phone


def _resolve_public_base_url() -> str:
    explicit = os.getenv("WEBHOOK_BASE_URL", "").strip().rstrip("/")
    if explicit:
        return explicit

    outbound = os.getenv("OUTBOUND_TWIML_URL", "").strip()
    if outbound:
        parsed = urlparse(outbound)
        if parsed.scheme and parsed.netloc:
            return f"{parsed.scheme}://{parsed.netloc}"

    raise RuntimeError("Missing WEBHOOK_BASE_URL (or valid OUTBOUND_TWIML_URL) in environment.")


def _default_recording_callback_url() -> str:
    explicit = os.getenv("RECORDING_STATUS_CALLBACK_URL", "").strip()
    if explicit:
        return explicit
    base = _resolve_public_base_url()
    return f"{base}/voice/recording"


def _build_twiml_url(base_twiml_url: str, patient_name: str, row_index: int, patient_id: str) -> str:
    if not base_twiml_url:
        return ""
    return _append_query(
        base_twiml_url,
        {
            "patient_name": patient_name,
            "row_index": str(row_index),
            "patient_id": patient_id,
        },
    )


def _twilio_client() -> Client:
    sid = os.getenv("TWILIO_ACCOUNT_SID", "").strip()
    token = os.getenv("TWILIO_AUTH_TOKEN", "").strip()
    if not sid or not token:
        raise RuntimeError("Missing TWILIO_ACCOUNT_SID or TWILIO_AUTH_TOKEN in environment.")
    return Client(sid, token)


def initialize_queue_state(
    *,
    csv_file: Path,
    state_file: Path,
    name_column: str | None,
    phone_column: str | None,
    start_index: int,
) -> dict[str, Any]:
    with csv_file.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        headers = reader.fieldnames or []
        name_col, phone_col = _detect_columns(headers, name_column, phone_column)

        rows: list[dict[str, Any]] = []
        for idx, row in enumerate(reader):
            if idx < start_index:
                continue
            raw_name = (row.get(name_col) or "").strip()
            raw_phone = (row.get(phone_col) or "").strip()
            patient_id = (row.get("Patient ID") or row.get("patient_id") or "").strip()
            rows.append(
                {
                    "row_index": idx,
                    "patient_name": raw_name,
                    "phone_number": raw_phone,
                    "patient_id": patient_id,
                }
            )

    state = {
        "running": True,
        "cursor": 0,
        "active_call_sid": None,
        "name_column": name_col,
        "phone_column": phone_col,
        "rows": rows,
        "last_status": "initialized",
    }
    _save_state(state_file, state)
    return state


def place_next_call(state_file: Path) -> bool:
    state = _load_state(state_file)
    if not state or not state.get("running", False):
        return False

    client = _twilio_client()
    rows = state.get("rows", [])
    cursor = int(state.get("cursor", 0))

    active_sid = str(state.get("active_call_sid") or "").strip()
    if active_sid:
        try:
            active_call = client.calls(active_sid).fetch()
            active_status = str(active_call.status or "").strip().lower()
            if active_status in TERMINAL_STATUSES:
                state.update(
                    {
                        "cursor": cursor + 1,
                        "active_call_sid": None,
                        "last_status": f"reconciled_terminal_{active_status}",
                        "last_terminal_status": active_status,
                    }
                )
                _save_state(state_file, state)
                cursor = int(state.get("cursor", cursor + 1))
            else:
                state.update({"last_status": f"waiting_{active_status or 'active'}"})
                _save_state(state_file, state)
                return False
        except TwilioRestException:
            # If fetch fails, keep queue safe and wait for callback/manual retry.
            state.update({"last_status": "waiting_callback_or_retry"})
            _save_state(state_file, state)
            return False

    from_number = os.getenv("CALL_FROM_NUMBER", "").strip()
    base_twiml_url = os.getenv("OUTBOUND_TWIML_URL", "").strip()
    fallback_say = os.getenv("CALL_CSV_FALLBACK_SAY", "").strip()
    record = _is_truthy(os.getenv("RECORD_CALLS", "true"))
    recording_status_callback = _default_recording_callback_url()
    public_base = _resolve_public_base_url()
    status_callback = f"{public_base}/voice/events?csv_queue=1"

    if not from_number:
        raise RuntimeError("Missing CALL_FROM_NUMBER in environment.")
    if not base_twiml_url and not fallback_say:
        raise RuntimeError("Missing OUTBOUND_TWIML_URL and CALL_CSV_FALLBACK_SAY for call content.")

    while cursor < len(rows):
        row = rows[cursor]
        name = str(row.get("patient_name", "")).strip()
        phone = str(row.get("phone_number", "")).strip()

        if not name or not phone:
            state.update(
                {
                    "cursor": cursor + 1,
                    "last_status": "skipped_missing_name_or_phone",
                    "last_row_index": row.get("row_index", cursor),
                }
            )
            _save_state(state_file, state)
            cursor += 1
            continue

        if not _is_e164(phone):
            state.update(
                {
                    "cursor": cursor + 1,
                    "last_status": "skipped_invalid_phone",
                    "last_row_index": row.get("row_index", cursor),
                    "last_phone": phone,
                    "last_name": name,
                }
            )
            _save_state(state_file, state)
            cursor += 1
            continue

        twiml_url = _build_twiml_url(
            base_twiml_url,
            name,
            int(row.get("row_index", cursor)),
            str(row.get("patient_id", "")),
        )

        kwargs: dict[str, Any] = {
            "to": phone,
            "from_": from_number,
            "status_callback": status_callback,
            "status_callback_method": "POST",
            "status_callback_event": ["initiated", "ringing", "answered", "completed"],
        }

        if twiml_url:
            kwargs["url"] = twiml_url
        else:
            safe_message = fallback_say.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            kwargs["twiml"] = f"<Response><Say>{safe_message}</Say></Response>"

        if record:
            kwargs["record"] = True
            kwargs["recording_channels"] = "mono"
            kwargs["recording_status_callback_event"] = ["completed"]
            if recording_status_callback:
                kwargs["recording_status_callback"] = recording_status_callback
                kwargs["recording_status_callback_method"] = "POST"

        call = client.calls.create(**kwargs)

        state.update(
            {
                "cursor": cursor,
                "active_call_sid": str(call.sid),
                "last_status": "called",
                "last_row_index": row.get("row_index", cursor),
                "last_phone": phone,
                "last_name": name,
                "last_call_sid": str(call.sid),
            }
        )
        _save_state(state_file, state)
        print(f"[queued] row={row.get('row_index', cursor)} name={name} to={phone} call_sid={call.sid}")
        return True

    state.update({"running": False, "active_call_sid": None, "last_status": "completed"})
    _save_state(state_file, state)
    print("CSV queue complete.")
    return False


def handle_status_callback(form: dict[str, Any], state_file: Path) -> bool:
    state = _load_state(state_file)
    if not state or not state.get("running", False):
        return False

    active_sid = str(state.get("active_call_sid") or "").strip()
    if not active_sid:
        return False

    call_sid = str(form.get("CallSid") or "").strip()
    call_status = str(form.get("CallStatus") or "").strip().lower()

    if call_sid != active_sid:
        return False
    if call_status not in TERMINAL_STATUSES:
        return False

    current_cursor = int(state.get("cursor", 0))
    state.update(
        {
            "cursor": current_cursor + 1,
            "active_call_sid": None,
            "last_status": f"terminal_{call_status}",
            "last_terminal_status": call_status,
        }
    )
    _save_state(state_file, state)

    return place_next_call(state_file)
