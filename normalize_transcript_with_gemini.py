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
DEFAULT_CONVERSATION_DIR = os.getenv("CONVERSATION_DIR", "conversation")
NORMALIZER_API_KEY_ENV = "TRANSCRIPT_NORMALIZER_GEMINI_API_KEY"
OUTCOME_LABELS = {
    "voicemail",
    "wrong_number",
    "opted_out",
    "scheduled",
    "incomplete",
    "escalated",
    "completed",
}
DEFAULT_OUTCOME = "incomplete"


def extract_agent_reference_lines(conversation_text: str) -> list[str]:
    """Extract ordered agent> lines from conversation logs for prompt grounding."""
    refs: list[str] = []

    # conversation format examples:
    # [12.34-15.67] agent> text
    # agent> text
    agent_pattern = re.compile(r"^(?:\[[^\]]+\]\s*)?agent>\s*(.+)$", re.IGNORECASE)

    for line in conversation_text.splitlines():
        line = line.strip()
        if not line:
            continue

        agent_match = agent_pattern.match(line)
        if agent_match:
            agent_text = agent_match.group(1).strip()
            if agent_text:
                refs.append(f"agent> {agent_text}")

    return refs


def extract_call_sid_from_name(file_name: str) -> str | None:
    match = re.search(r"(CA[A-Za-z0-9]+)", file_name)
    if match:
        return match.group(1)
    return None


def resolve_conversation_file_for_input(input_file: Path, conversation_dir: Path) -> Path | None:
    call_sid = extract_call_sid_from_name(input_file.name)
    if not call_sid or not conversation_dir.exists():
        return None

    candidates = sorted(
        conversation_dir.glob(f"*{call_sid}*.txt"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    input_resolved = input_file.resolve()
    candidates = [p for p in candidates if p.resolve() != input_resolved]
    if not candidates:
        return None

    return candidates[0]


def build_conversation_pair_block(conversation_file: Path | None) -> str:
    if conversation_file is None:
        return "- (no matching conversation file found)"

    text = conversation_file.read_text(encoding="utf-8", errors="ignore")
    refs = extract_agent_reference_lines(text)
    if not refs:
        return "- (no agent lines found in conversation file)"

    # Keep prompt size manageable while preserving recent context.
    tail_refs = refs[-120:]
    return "\n".join(f"- {line}" for line in tail_refs)


def build_prompt(raw_transcript: str, agent_reference_block: str) -> str:

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
AGENT REFERENCE LINES (GROUNDING)
----------------------------------------

These are extracted from the matching file in conversation/ as agent> lines only.
Use them as the highest-priority grounding to preserve agent question wording and intent.

{agent_reference_block}

----------------------------------------
OUTCOME CLASSIFICATION (FIRST LINE, STRICT)
----------------------------------------

You MUST output the FIRST line exactly in this format:
outcome=<label>

Allowed labels:
- voicemail
- wrong_number
- opted_out
- scheduled
- incomplete
- escalated
- completed

Definitions:
- voicemail: no real user response; no patient interaction after opener.
- wrong_number: user answers and clearly denies being the target patient.
- opted_out: target patient is reached/confirmed but refuses check-in/refill or asks to stop.
- scheduled: target patient is reached but asks for callback/reschedule and callback timing is agreed.
- incomplete: check-in starts but all 14 required questions are not completed.
- escalated: concerning medical disclosure requiring human follow-up.
- completed: all 14 required questions completed and no escalation trigger.

Decision priority if multiple seem possible:
wrong_number > voicemail > opted_out > scheduled > escalated > completed > incomplete

If uncertain, choose: incomplete

----------------------------------------
STRICT RULES (VERY IMPORTANT)
----------------------------------------

- Do NOT add new information
- Do NOT hallucinate missing answers
- Do NOT change meaning
- Only reorganize, clean, and correct structure
- Do NOT rewrite user answers/content.
- Change AGENT wording only when needed to correct question phrasing using conversation grounding.
- Keep non-question AGENT content unchanged unless minimal cleanup/splitting is needed.
- For AGENT question variants/typos/singular-plural drift, align wording to the grounded conversation intent.

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

3b. Question phrase correction scope:
    - Correct ONLY AGENT question phrases that are malformed or variant forms.
    - Preserve surrounding non-question content as much as possible.
    - Do not paraphrase USER lines.
    - Prefer grounded question wording from agent>...user> references.

3c. ONE canonical question per [AGENT] line (STRICT):
    - Each of the 14 required questions MUST appear on its own separate [AGENT] line.
    - NEVER combine two canonical questions into a single [AGENT] line.
    - If the transcript has two questions merged in one line, split them into
      two separate [AGENT] lines.
    - Acknowledgment + question in the same line is perfectly fine.
    
    Example of WRONG output:
    [AGENT]: Got it, 198 pounds. What's your height in feet and inches? How much weight have you lost this past month in pounds?
    
    Example of CORRECT output:
    [AGENT]: Got it, 198 pounds. What's your height in feet and inches?
    [AGENT]: How much weight have you lost this past month in pounds?


    ----------------------------------------
THE 14 CANONICAL QUESTIONS (REFERENCE)
----------------------------------------

These are the exact 14 questions the agent must ask, in order:

1. How have you been feeling overall?
2. What's your current weight in pounds?
3. What's your height in feet and inches?
4. How much weight have you lost this past month (in pounds)?
5. Any side effects from your medication this month?
6. Are you satisfied with your rate of weight loss?
7. What's your goal weight in pounds?
8. Any requests about your dosage?
9. Have you started any new medications or supplements since last month?
10. Do you have any new medical conditions since your last check-in?
11. Any new allergies?
12. Any surgeries since your last check-in?
13. Any questions for your doctor?
14. Has your shipping address changed?

Use these as reference when cleaning agent lines. Each canonical question MUST
appear on its own [AGENT] line — never merged with another canonical question.

----------------------------------------
3d. AGENT SELF-ANSWER DETECTION (CRITICAL)
----------------------------------------

Sometimes the agent answers its own question in the same line without waiting
for the user. This causes the extractor to misalign answers to questions.

Example of problematic input:
[AGENT]: Got it, updated. Any new allergies? No new allergies, thanks. Any surgeries since your last check-in?
[USER]: Yeah, I had a spinal surgery.

In this case:
- The agent asked Q11 (allergies) AND answered it itself ("No new allergies")
- The agent then asked Q12 (surgeries) in the same line
- The USER response "Yeah, I had a spinal surgery" belongs to Q12, not Q11

You MUST split this into:
[AGENT]: Got it, updated. Any new allergies?
[USER]: No.
[AGENT]: Any surgeries since your last check-in?
[USER]: Yeah, I had a spinal surgery.

RULES for self-answer detection:
- If an [AGENT] line contains a canonical question FOLLOWED BY what appears to
  be an answer ("No new allergies", "None", "Same address", "No change") FOLLOWED
  BY another canonical question — this is a self-answer pattern.
- Split it into three parts: [AGENT] question → [USER] with the implied answer
  → [AGENT] next question.
- The implied [USER] answer should be "No." or "Yes." ONLY — the minimal
  factual answer implied by the agent's acknowledgment. Do NOT fabricate detail.
- This is the ONLY case where you may insert a [USER] line that was not in the
  original transcript. i repeat: ONLY in this specific case.
- Only apply this when the agent's self-answer is a simple binary (no/none/yes/
  same/unchanged). If the implied answer is complex or ambiguous, do NOT insert
  a [USER] line — instead just split the [AGENT] lines and leave the USER
  response to follow naturally.

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

"outcome=incomplete
[AGENT]: ...
[USER]: ...
[AGENT]: ...
[USER]: ...\"

- First line MUST be outcome=<label>
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


def ensure_outcome_first_line(text: str) -> str:
    """Ensure first line is outcome=<allowed_label>; fallback to outcome=incomplete."""
    lines = [ln.rstrip() for ln in text.splitlines() if ln.strip()]
    if not lines:
        return f"outcome={DEFAULT_OUTCOME}"

    outcome_line: str | None = None
    outcome_pattern = re.compile(r"^outcome\s*=\s*([a-z_]+)\s*$", re.IGNORECASE)

    remaining: list[str] = []
    for ln in lines:
        m = outcome_pattern.match(ln.strip())
        if m and outcome_line is None:
            label = m.group(1).lower()
            if label in OUTCOME_LABELS:
                outcome_line = f"outcome={label}"
                continue
        remaining.append(ln)

    if outcome_line is None:
        outcome_line = f"outcome={DEFAULT_OUTCOME}"

    return "\n".join([outcome_line, *remaining]).strip()


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
    conversation_dir = Path(DEFAULT_CONVERSATION_DIR).resolve()
    conversation_file = resolve_conversation_file_for_input(input_file, conversation_dir)
    agent_reference_block = build_conversation_pair_block(conversation_file)
    prompt = build_prompt(raw_text, agent_reference_block)

    client = genai.Client(api_key=api_key)
    response = client.models.generate_content(model=model, contents=prompt)
    output_text = sanitize_model_output(response.text or "")
    output_text = ensure_outcome_first_line(output_text)
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
