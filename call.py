#!/usr/bin/env python3
"""Place a phone call using Twilio.

Examples:
  python call.py --to +15551234567 --from +15557654321 --twiml-url https://handler.twilio.com/twiml/EHxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
  python call.py --to +15551234567 --from +15557654321 --say "Hello from Carecaller"

Environment variables supported:
  TWILIO_ACCOUNT_SID
  TWILIO_AUTH_TOKEN
    CALL_TO_NUMBER
    CALL_FROM_NUMBER
    OUTBOUND_TWIML_URL
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import Optional

from dotenv import load_dotenv
from twilio.base.exceptions import TwilioRestException
from twilio.rest import Client

load_dotenv()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Place an outbound call with Twilio."
    )
    parser.add_argument(
        "--to",
        default=os.getenv("CALL_TO_NUMBER", ""),
        help="Destination phone number in E.164 format (default: CALL_TO_NUMBER env var).",
    )
    parser.add_argument(
        "--from",
        dest="from_number",
        default=os.getenv("CALL_FROM_NUMBER", ""),
        help="Your Twilio phone number in E.164 format (default: CALL_FROM_NUMBER env var).",
    )
    parser.add_argument(
        "--twiml-url",
        default=os.getenv("OUTBOUND_TWIML_URL", ""),
        help="Public URL returning TwiML instructions for the call (default: OUTBOUND_TWIML_URL env var).",
    )
    parser.add_argument(
        "--say",
        default="",
        help="Fallback message spoken to callee if --twiml-url is not provided.",
    )
    parser.add_argument(
        "--account-sid",
        default=os.getenv("TWILIO_ACCOUNT_SID", ""),
        help="Twilio Account SID (default: TWILIO_ACCOUNT_SID env var).",
    )
    parser.add_argument(
        "--auth-token",
        default=os.getenv("TWILIO_AUTH_TOKEN", ""),
        help="Twilio Auth Token (default: TWILIO_AUTH_TOKEN env var).",
    )
    return parser.parse_args()


def validate_args(args: argparse.Namespace) -> Optional[str]:
    if not args.to:
        return "Missing destination number. Use --to or set CALL_TO_NUMBER."
    if not args.from_number:
        return "Missing Twilio source number. Use --from or set CALL_FROM_NUMBER."
    if not args.account_sid:
        return "Missing Twilio Account SID. Use --account-sid or set TWILIO_ACCOUNT_SID."
    if not args.auth_token:
        return "Missing Twilio Auth Token. Use --auth-token or set TWILIO_AUTH_TOKEN."
    if not args.twiml_url and not args.say:
        return "Provide either --twiml-url or --say so Twilio knows what to do when the call connects."
    return None


def place_call(args: argparse.Namespace) -> str:
    client = Client(args.account_sid, args.auth_token)

    create_kwargs = {
        "to": args.to,
        "from_": args.from_number,
    }

    if args.twiml_url:
        create_kwargs["url"] = args.twiml_url
    else:
        # Twilio supports inline TwiML through `twiml`.
        safe_message = args.say.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        create_kwargs["twiml"] = f"<Response><Say>{safe_message}</Say></Response>"

    call = client.calls.create(**create_kwargs)
    return call.sid


def main() -> int:
    args = parse_args()
    validation_error = validate_args(args)
    if validation_error:
        print(f"Error: {validation_error}", file=sys.stderr)
        return 2

    try:
        call_sid = place_call(args)
    except TwilioRestException as exc:
        print("Twilio API error while placing call:", file=sys.stderr)
        print(f"  status={exc.status} code={exc.code} message={exc.msg}", file=sys.stderr)
        return 1
    except Exception as exc:  # pragma: no cover - defensive fallback
        print(f"Unexpected error: {exc}", file=sys.stderr)
        return 1

    print("Call initiated successfully.")
    print(f"Call SID: {call_sid}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
