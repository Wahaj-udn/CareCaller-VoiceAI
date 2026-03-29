#!/usr/bin/env python3
"""Build labeled final transcripts by aligning Whisper segments with conversation turns."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
import re
from statistics import median
from typing import Optional


WHISPER_SEGMENT_RE = re.compile(
    r"^\[(?P<start>\d{2}:\d{2}:\d{2}\.\d{3})\s*->\s*(?P<end>\d{2}:\d{2}:\d{2}\.\d{3})\]\s*(?P<text>.+)$"
)
CONVERSATION_LINE_RE = re.compile(
    r"^(?:\[(?P<start>[\d.]+)-(?P<end>[\d.]+)\]\s*)?(?P<speaker>agent|user)>(?P<text>.+)$"
)
CALL_SID_RE = re.compile(r"CA[A-Za-z0-9]{32}")

AGENT_FILLER_PHRASES = {
    "ok",
    "okay",
    "got it",
    "thanks",
    "thank you",
    "that s good to hear",
    "thats good to hear",
    "great",
    "no problem",
}


@dataclass
class WhisperSegment:
    start_s: float
    end_s: float
    text: str


@dataclass
class ConversationTurn:
    speaker: str
    text: str
    start_s: Optional[float] = None
    end_s: Optional[float] = None


@dataclass
class FinalLine:
    start_s: float
    end_s: float
    speaker: str
    text: str


def _parse_hhmmss_ms(text: str) -> float:
    hh, mm, rest = text.split(":")
    ss, ms = rest.split(".")
    return int(hh) * 3600 + int(mm) * 60 + int(ss) + int(ms) / 1000.0


def _format_compact_seconds(seconds: float) -> str:
    return f"{max(0.0, seconds):.3f}".rstrip("0").rstrip(".") or "0"


def _normalize(text: str) -> str:
    lowered = text.lower()
    lowered = re.sub(r"[^a-z0-9\s]", " ", lowered)
    lowered = re.sub(r"\s+", " ", lowered).strip()
    return lowered


def _is_agent_filler_phrase(text: str) -> bool:
    norm = _normalize(text)
    if not norm:
        return False

    words = norm.split()
    if len(words) > 6:
        return False

    return norm in AGENT_FILLER_PHRASES


def parse_whisper_transcript(path: Path) -> tuple[list[str], list[WhisperSegment]]:
    header: list[str] = []
    segments: list[WhisperSegment] = []

    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        match = WHISPER_SEGMENT_RE.match(stripped)
        if not match:
            if stripped or not segments:
                header.append(line)
            continue

        segments.append(
            WhisperSegment(
                start_s=_parse_hhmmss_ms(match.group("start")),
                end_s=_parse_hhmmss_ms(match.group("end")),
                text=match.group("text").strip(),
            )
        )

    return header, segments


def parse_conversation_file(path: Path) -> tuple[Optional[str], list[ConversationTurn]]:
    call_sid: Optional[str] = None
    turns: list[ConversationTurn] = []

    for raw in path.read_text(encoding="utf-8").splitlines():
        stripped = raw.strip()
        if not stripped:
            continue

        if stripped.startswith("# call_sid="):
            call_sid = stripped.split("=", 1)[-1].strip() or call_sid
            continue

        match = CONVERSATION_LINE_RE.match(stripped)
        if not match:
            continue

        start_s = float(match.group("start")) if match.group("start") is not None else None
        end_s = float(match.group("end")) if match.group("end") is not None else None
        turns.append(
            ConversationTurn(
                speaker=match.group("speaker"),
                text=match.group("text").strip(),
                start_s=start_s,
                end_s=end_s,
            )
        )

    return call_sid, turns


def extract_call_sid_from_name(path: Path) -> Optional[str]:
    match = CALL_SID_RE.search(path.name)
    return match.group(0) if match else None


def find_conversation_for_call(call_sid: str, conversation_dir: Path) -> Optional[Path]:
    candidates = sorted(conversation_dir.glob(f"*_{call_sid}.txt"), key=lambda p: p.stat().st_mtime, reverse=True)
    return candidates[0] if candidates else None


def _time_overlap_ratio(a_start: float, a_end: float, b_start: float, b_end: float) -> float:
    left = max(a_start, b_start)
    right = min(a_end, b_end)
    if right <= left:
        return 0.0
    overlap = right - left
    span = max(a_end - a_start, 1e-6)
    return overlap / span


def _choose_speaker(
    seg: WhisperSegment,
    turns: list[ConversationTurn],
    last_index: int,
    min_score: float,
    time_offset_s: float,
) -> tuple[str, int]:
    # Rule 2: short, generic acknowledgment phrases are likely agent turns.
    if _is_agent_filler_phrase(seg.text):
        return "agent", last_index

    # First, use adjusted agent timestamp ranges when available.
    agent_timed = [t for t in turns if t.speaker == "agent" and t.start_s is not None and t.end_s is not None]
    if agent_timed:
        for turn in agent_timed:
            start = turn.start_s + time_offset_s
            end = turn.end_s + time_offset_s
            overlap = _time_overlap_ratio(seg.start_s, seg.end_s, start, end)
            mid = (seg.start_s + seg.end_s) / 2.0
            if overlap >= 0.25 or (start <= mid <= end):
                return "agent", last_index
        return "user", last_index

    norm_seg = _normalize(seg.text)

    # Search near previous alignment for stable monotonic matching.
    start_i = max(0, last_index - 2)
    end_i = min(len(turns), last_index + 12) if turns else 0
    best_i = -1
    best_score = 0.0

    for i in range(start_i, end_i):
        turn = turns[i]
        if not turn.text:
            continue
        ratio = SequenceMatcher(None, norm_seg, _normalize(turn.text)).ratio()

        # Agent time-ranges are reliable; boost score when segment overlaps.
        if turn.start_s is not None and turn.end_s is not None:
            ratio += 0.35 * _time_overlap_ratio(seg.start_s, seg.end_s, turn.start_s, turn.end_s)
            mid = (seg.start_s + seg.end_s) / 2.0
            if turn.start_s <= mid <= turn.end_s:
                ratio += 0.2

        if ratio > best_score:
            best_score = ratio
            best_i = i

    if best_i >= 0 and best_score >= min_score:
        return turns[best_i].speaker, best_i

    # Fallback using agent explicit time-ranges.
    for turn in turns:
        if turn.speaker != "agent" or turn.start_s is None or turn.end_s is None:
            continue
        if _time_overlap_ratio(seg.start_s, seg.end_s, turn.start_s, turn.end_s) >= 0.25:
            return "agent", last_index

    return "user", last_index


def build_final_transcript(
    whisper_path: Path,
    conversation_path: Path,
    output_dir: Path,
    min_score: float = 0.42,
) -> Path:
    header, segments = parse_whisper_transcript(whisper_path)
    _, turns = parse_conversation_file(conversation_path)
    time_offset_s = estimate_time_offset_seconds(segments, turns)

    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / whisper_path.name

    lines: list[str] = []
    lines.extend([h for h in header if h.strip()])
    lines.append(f"conversation_source={conversation_path.name}")
    lines.append("")

    last_idx = 0
    final_lines: list[FinalLine] = []
    for seg in segments:
        speaker, last_idx = _choose_speaker(
            seg,
            turns,
            last_idx,
            min_score=min_score,
            time_offset_s=time_offset_s,
        )
        final_lines.append(
            FinalLine(
                start_s=seg.start_s,
                end_s=seg.end_s,
                speaker=speaker,
                text=seg.text,
            )
        )

    merged = merge_consecutive_same_speaker(final_lines)
    for item in merged:
        lines.append(
            f"[{_format_compact_seconds(item.start_s)}-{_format_compact_seconds(item.end_s)}] {item.speaker}> {item.text}"
        )

    output_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return output_path


def build_for_whisper_file(
    whisper_file: Path,
    conversation_dir: Path,
    output_dir: Path,
    min_score: float = 0.42,
) -> Optional[Path]:
    call_sid = extract_call_sid_from_name(whisper_file)
    if not call_sid:
        return None

    conversation_file = find_conversation_for_call(call_sid, conversation_dir)
    if not conversation_file:
        return None

    return build_final_transcript(
        whisper_path=whisper_file,
        conversation_path=conversation_file,
        output_dir=output_dir,
        min_score=min_score,
    )


def merge_consecutive_same_speaker(lines: list[FinalLine]) -> list[FinalLine]:
    if not lines:
        return []

    merged: list[FinalLine] = [lines[0]]
    for line in lines[1:]:
        prev = merged[-1]
        if line.speaker == prev.speaker:
            prev.end_s = max(prev.end_s, line.end_s)
            prev.text = f"{prev.text} {line.text}".strip()
        else:
            merged.append(line)

    return merged


def estimate_time_offset_seconds(segments: list[WhisperSegment], turns: list[ConversationTurn]) -> float:
    """Estimate whisper-vs-conversation timeline offset using high-confidence agent text matches."""
    agent_turns = [t for t in turns if t.speaker == "agent" and t.start_s is not None and t.text]
    if not agent_turns or not segments:
        return 0.0

    deltas: list[float] = []
    for turn in agent_turns:
        turn_norm = _normalize(turn.text)
        best_ratio = 0.0
        best_start: Optional[float] = None
        for seg in segments:
            ratio = SequenceMatcher(None, _normalize(seg.text), turn_norm).ratio()
            if ratio > best_ratio:
                best_ratio = ratio
                best_start = seg.start_s

        if best_start is not None and best_ratio >= 0.55:
            deltas.append(best_start - float(turn.start_s))

    if not deltas:
        return 0.0
    return float(median(deltas))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build final labeled transcript from whisper+conversation files")
    parser.add_argument("--whisper-file", help="Path to one whisper transcript file", default="")
    parser.add_argument("--whisper-dir", default="whisper_transcript", help="Whisper transcript directory")
    parser.add_argument("--conversation-dir", default="conversation", help="Conversation transcript directory")
    parser.add_argument("--output-dir", default="final_transcript", help="Output directory")
    parser.add_argument("--min-score", type=float, default=0.42, help="Minimum alignment score")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    whisper_dir = Path(args.whisper_dir)
    conversation_dir = Path(args.conversation_dir)
    output_dir = Path(args.output_dir)

    if args.whisper_file:
        path = build_for_whisper_file(
            whisper_file=Path(args.whisper_file),
            conversation_dir=conversation_dir,
            output_dir=output_dir,
            min_score=args.min_score,
        )
        if path is None:
            print("No matching conversation/call_sid found for whisper file.")
            return 1
        print(f"Final transcript saved: {path}")
        return 0

    latest = sorted(whisper_dir.glob("*.txt"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not latest:
        print("No whisper transcript files found.")
        return 0

    path = build_for_whisper_file(
        whisper_file=latest[0],
        conversation_dir=conversation_dir,
        output_dir=output_dir,
        min_score=args.min_score,
    )
    if path is None:
        print("No matching conversation/call_sid found for latest whisper transcript.")
        return 1

    print(f"Final transcript saved: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
