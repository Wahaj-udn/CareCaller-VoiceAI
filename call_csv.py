#!/usr/bin/env python3
"""Start or resume a callback-driven CSV outbound call queue.

Usage model:
1) Run this script once to initialize queue state and place the first call.
2) Keep `server.py` running so `/voice/events?csv_queue=1` callbacks advance queue.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

from csv_call_queue import initialize_queue_state, place_next_call

load_dotenv(override=True)

DEFAULT_STATE_FILE = ".call_csv_state.json"
PROJECT_ROOT = Path(__file__).resolve().parent


def _resolve_state_path(raw_path: str) -> Path:
    path = Path(raw_path)
    if path.is_absolute():
        return path
    return PROJECT_ROOT / path


def _is_truthy(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Start/resume callback-driven CSV outbound queue.")
    parser.add_argument(
        "--csv-file",
        default=os.getenv("CALL_CSV_PATH", "patient_checkin.csv"),
        help="Path to patient CSV file (default: CALL_CSV_PATH or patient_checkin.csv).",
    )
    parser.add_argument(
        "--name-column",
        default=os.getenv("CALL_CSV_NAME_COLUMN", ""),
        help="Optional explicit name column header. If omitted, auto-detection is used.",
    )
    parser.add_argument(
        "--phone-column",
        default=os.getenv("CALL_CSV_PHONE_COLUMN", ""),
        help="Optional explicit phone column header. If omitted, auto-detection is used.",
    )
    parser.add_argument(
        "--start-index",
        type=int,
        default=int(os.getenv("CALL_CSV_START_INDEX", "0") or "0"),
        help="Start row index for a fresh run (0-based).",
    )
    parser.add_argument(
        "--state-file",
        default=os.getenv("CALL_CSV_STATE_FILE", DEFAULT_STATE_FILE),
        help=f"Queue progress state file (default: CALL_CSV_STATE_FILE or {DEFAULT_STATE_FILE}).",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        default=_is_truthy(os.getenv("CALL_CSV_RESUME", "false")),
        help="Resume existing queue state without rebuilding from CSV.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=_is_truthy(os.getenv("CALL_CSV_DRY_RUN", "false")),
        help="Only initialize state and print summary. Do not place first call.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    csv_path = Path(args.csv_file)
    state_path = _resolve_state_path(args.state_file)

    try:
        if not args.resume:
            if not csv_path.exists():
                print(f"Error: CSV file not found: {csv_path}", file=sys.stderr)
                return 2

            state = initialize_queue_state(
                csv_file=csv_path,
                state_file=state_path,
                name_column=args.name_column or None,
                phone_column=args.phone_column or None,
                start_index=max(0, args.start_index),
            )
            total = len(state.get("rows", []))
            print(f"Queue initialized from {csv_path}. rows={total} start_index={max(0, args.start_index)}")
        else:
            print(f"Resuming queue from state file: {state_path}")

        if args.dry_run:
            print("Dry run complete. No calls were placed.")
            return 0

        started = place_next_call(state_path)
        if started:
            print("First pending call placed. Remaining progression will happen via /voice/events callbacks.")
        else:
            print("No call placed (already running, finished, or no valid rows left).")
        return 0
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
