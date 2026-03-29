#!/usr/bin/env python3
"""Normalize noisy call transcripts using Gemini text generation."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import re

from dotenv import load_dotenv
from google import genai

load_dotenv()

DEFAULT_MODEL = os.getenv("TRANSCRIPT_NORMALIZER_MODEL", "gemini-2.5-flash")
DEFAULT_OUTPUT_DIR = os.getenv("NORMALIZED_TRANSCRIPT_DIR", "normalized_transcript")
DEFAULT_INPUT_DIR = os.getenv("FINAL_TRANSCRIPT_DIR", "final_transcript")
NORMALIZER_API_KEY_ENV = "TRANSCRIPT_NORMALIZER_GEMINI_API_KEY"


def build_prompt(raw_transcript: str) -> str:
    return f"""You are a transcript normalization engine for a healthcare AI system.

CONTEXT:
The input is a raw conversation transcript generated from speech-to-text (Whisper-like system).
It contains timestamps, inconsistent speaker labels, and transcription noise.

The output must be a clean conversation using ONLY:
- [AGENT]: for the AI caller
- [USER]: for the patient

----------------------------------------
⚠️ INPUT ISSUES
----------------------------------------

The transcript may contain:
- system messages (e.g., \"trial account\", \"connecting you\")
- incorrect speaker labels (user speaking agent phrases, etc.)
- multiple speakers in one line
- multiple questions in one agent line
- broken or merged sentences
- transcription noise (random numbers, repeated words)

----------------------------------------
🎯 YOUR TASK
----------------------------------------

Transform the transcript into a clean conversational format.

----------------------------------------
STRICT RULES (VERY IMPORTANT)
----------------------------------------

- Do NOT add new information
- Do NOT hallucinate missing answers
- Do NOT change meaning
- Only reorganize, clean, and correct structure

----------------------------------------
CLEANING RULES
----------------------------------------

1. Remove system-level messages completely:
   Examples:
   - \"You have a trial account\"
   - \"Connecting you...\"

2. Fix speaker labels:
   - Agent asks questions and gives acknowledgments
   - User provides responses
   - If a line contains both → split correctly

3. Split lines when:
   - multiple questions exist
   - question + acknowledgment exist
   - mixed speaker content exists

4. Merge lines when:
   - same speaker continues naturally
    - all consecutive identical tags must be merged into a single line

5. Remove obvious noise:
   - duplicated fragments

6. Preserve natural conversational tone

7. Handle numeric-field exchanges carefully (high priority):
    For questions that expect numbers (weight, height, pounds lost, goal weight, dosage amount, address numbers):
    - If a line is asking/confirming a numeric field, classify as [AGENT]
    - If a line is providing the numeric value, classify as [USER]
    - If both happen in one line, split into two lines
    - If transcription is inconsistent (e.g., "59", "5n", "1:45", repeated numbers), infer intent from context and nearby turns
    - Treat short acknowledgments after numeric answers ("Got it", "Okay, 145") as [AGENT]

8. Prefer semantic role over raw tag text:
    - Even if original tag says user/agent, correct it based on what the sentence is doing
    - Question/confirmation/recap -> usually [AGENT]
    - Value/report/symptom answer -> usually [USER]

----------------------------------------
OUTPUT FORMAT (STRICT)
----------------------------------------

Return a SINGLE STRING exactly like this:

\"[AGENT]: ...
[USER]: ...
[AGENT]: ...
[USER]: ...\"

- No timestamps
- No extra commentary
- No JSON
- No explanation

make sure to make the tags [AGENT] and [USER] are put in the correct places according to the content, even if the original transcript had them wrong.
Pay extra attention to numeric Q&A turns and correction statements (e.g., "No, I said 5'10"), and place speaker tags by intent.
----------------------------------------
INPUT:
{raw_transcript}
"""


def sanitize_model_output(text: str) -> str:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        cleaned = "\n".join(lines).strip()
    return cleaned


def merge_consecutive_speaker_tags(text: str) -> str:
    """Merge all consecutive [AGENT]/[USER] lines into single lines per run."""
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    pattern = re.compile(r"^\[(AGENT|USER)\]\s*:\s*(.*)$", re.IGNORECASE)

    merged: list[str] = []
    current_speaker: str | None = None
    current_text: str = ""

    def flush() -> None:
        nonlocal current_speaker, current_text
        if current_speaker is not None:
            merged.append(f"[{current_speaker}]: {current_text.strip()}")
            current_speaker = None
            current_text = ""

    for line in lines:
        m = pattern.match(line)
        if m:
            speaker = m.group(1).upper()
            content = m.group(2).strip()
            if current_speaker == speaker:
                current_text = f"{current_text} {content}".strip()
            else:
                flush()
                current_speaker = speaker
                current_text = content
            continue

        # Non-tagged lines are appended to the current speaker block if present.
        if current_speaker is not None:
            current_text = f"{current_text} {line}".strip()
        else:
            merged.append(line)

    flush()
    return "\n".join(merged).strip()


def get_normalizer_api_key() -> str:
    """Prefer a dedicated normalizer key, fallback to GEMINI_API_KEY."""
    key = os.getenv(NORMALIZER_API_KEY_ENV, "").strip()
    if key:
        return key
    return os.getenv("GEMINI_API_KEY", "").strip()


def normalize_transcript(input_file: Path, output_file: Path, model: str) -> Path:
    api_key = get_normalizer_api_key()
    if not api_key:
        raise RuntimeError(
            "Missing transcript normalizer API key. Set TRANSCRIPT_NORMALIZER_GEMINI_API_KEY "
            "(or GEMINI_API_KEY as fallback)."
        )

    raw_text = input_file.read_text(encoding="utf-8")
    prompt = build_prompt(raw_text)

    client = genai.Client(api_key=api_key)
    response = client.models.generate_content(model=model, contents=prompt)
    output_text = sanitize_model_output(response.text or "")
    output_text = merge_consecutive_speaker_tags(output_text)

    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.write_text(output_text + "\n", encoding="utf-8")
    return output_file


def resolve_input_file(input_file_arg: str, input_dir_arg: str) -> Path:
    if input_file_arg:
        input_file = Path(input_file_arg).resolve()
        if not input_file.exists():
            raise FileNotFoundError(f"Input file not found: {input_file}")
        return input_file

    input_dir = Path(input_dir_arg).resolve()
    if not input_dir.exists():
        raise FileNotFoundError(f"Input directory not found: {input_dir}")

    candidates = sorted(input_dir.glob("*.txt"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not candidates:
        raise FileNotFoundError(f"No transcript files found in: {input_dir}")
    return candidates[0]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Normalize a transcript file with Gemini")
    parser.add_argument(
        "--input-file",
        default="",
        help="Path to transcript file. If omitted, newest .txt from --input-dir is used.",
    )
    parser.add_argument(
        "--input-dir",
        default=DEFAULT_INPUT_DIR,
        help="Directory to scan for newest transcript when --input-file is omitted.",
    )
    parser.add_argument("--output-file", default="", help="Optional explicit output file path")
    parser.add_argument("--model", default=DEFAULT_MODEL, help="Gemini text model")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        input_file = resolve_input_file(args.input_file, args.input_dir)
    except FileNotFoundError as exc:
        raise SystemExit(str(exc))

    if args.output_file:
        output_file = Path(args.output_file).resolve()
    else:
        out_dir = Path(DEFAULT_OUTPUT_DIR).resolve()
        output_file = out_dir / f"{input_file.stem}.normalized.txt"

    result = normalize_transcript(input_file=input_file, output_file=output_file, model=args.model)
    print(f"Normalized transcript saved: {result}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
