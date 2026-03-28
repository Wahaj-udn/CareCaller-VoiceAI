#!/usr/bin/env python3
"""Local Faster-Whisper transcription helpers for call recordings."""

from __future__ import annotations

import argparse
from functools import lru_cache
import os
from pathlib import Path
import sys
from typing import Optional

from faster_whisper import WhisperModel


_DLL_DIR_HANDLES: list[object] = []


@lru_cache(maxsize=4)
def _get_model(model_name: str, device: str, compute_type: str) -> WhisperModel:
    return WhisperModel(model_name, device=device, compute_type=compute_type)


def _is_truthy(value: str) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


@lru_cache(maxsize=1)
def _configure_windows_cuda_dll_search_paths() -> None:
    if os.name != "nt":
        return

    # NVIDIA pip wheels place runtime DLLs under these folders.
    site_packages = Path(sys.prefix) / "Lib" / "site-packages" / "nvidia"
    candidate_bins = [
        site_packages / "cublas" / "bin",
        site_packages / "cuda_nvrtc" / "bin",
        site_packages / "cuda_runtime" / "bin",
        site_packages / "cudnn" / "bin",
    ]

    for bin_dir in candidate_bins:
        if not bin_dir.exists():
            continue

        # Keep PATH updated as a secondary fallback for some native dependency chains.
        current_path = os.environ.get("PATH", "")
        bin_dir_str = str(bin_dir)
        if bin_dir_str.lower() not in current_path.lower():
            os.environ["PATH"] = f"{bin_dir_str};{current_path}" if current_path else bin_dir_str

        try:
            handle = os.add_dll_directory(bin_dir_str)
            # IMPORTANT: keep handle alive for process lifetime; otherwise path can be removed.
            _DLL_DIR_HANDLES.append(handle)
        except (AttributeError, FileNotFoundError, OSError):
            # Keep best-effort behavior. Model init will raise if runtime is still unavailable.
            pass


def _format_timestamp(seconds: float) -> str:
    total_ms = int(max(0.0, seconds) * 1000)
    hours = total_ms // 3_600_000
    rem = total_ms % 3_600_000
    minutes = rem // 60_000
    rem = rem % 60_000
    secs = rem // 1000
    ms = rem % 1000
    return f"{hours:02}:{minutes:02}:{secs:02}.{ms:03}"


def transcript_path_for_recording(recording_file: Path, transcript_dir: Path) -> Path:
    return transcript_dir / f"{recording_file.stem}.txt"


def transcribe_recording_file(
    recording_file: Path,
    transcript_dir: Path,
    model_name: str = "small",
    device: str = "cuda",
    compute_type: str = "float16",
    language: Optional[str] = "en",
    beam_size: int = 5,
) -> Path:
    recording_file = recording_file.resolve()
    transcript_dir = transcript_dir.resolve()
    transcript_dir.mkdir(parents=True, exist_ok=True)

    if not recording_file.exists():
        raise FileNotFoundError(f"Recording not found: {recording_file}")

    output_file = transcript_path_for_recording(recording_file, transcript_dir)
    if output_file.exists():
        return output_file

    _configure_windows_cuda_dll_search_paths()
    fallback_to_cpu = _is_truthy(os.getenv("WHISPER_FALLBACK_TO_CPU", "false"))

    try:
        model = _get_model(model_name, device, compute_type)
        segments, info = model.transcribe(
            str(recording_file),
            language=language or None,
            beam_size=beam_size,
            vad_filter=True,
        )
    except Exception as exc:
        if not (fallback_to_cpu and device.lower() == "cuda"):
            raise

        error_text = str(exc).lower()
        runtime_issue = (
            "cublas" in error_text
            or "cudnn" in error_text
            or "cuda" in error_text
            or "dll" in error_text
        )
        if not runtime_issue:
            raise

        cpu_model = _get_model(model_name, "cpu", "int8")
        segments, info = cpu_model.transcribe(
            str(recording_file),
            language=language or None,
            beam_size=beam_size,
            vad_filter=True,
        )

    lines: list[str] = [
        f"source={recording_file.name}",
        f"language={getattr(info, 'language', 'unknown')}",
        f"duration={getattr(info, 'duration', 'unknown')}",
        "",
    ]

    for segment in segments:
        start = _format_timestamp(float(segment.start))
        end = _format_timestamp(float(segment.end))
        text = (segment.text or "").strip()
        if text:
            lines.append(f"[{start} -> {end}] {text}")

    if len(lines) == 4:
        lines.append("[no speech recognized]")

    output_file.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return output_file


def transcribe_newest_recording(
    recordings_dir: Path,
    transcript_dir: Path,
    model_name: str = "small",
    device: str = "cuda",
    compute_type: str = "float16",
    language: Optional[str] = "en",
    beam_size: int = 5,
) -> Optional[Path]:
    recordings_dir = recordings_dir.resolve()
    if not recordings_dir.exists():
        return None

    candidates = sorted(
        recordings_dir.glob("*.mp3"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not candidates:
        return None

    newest = candidates[0]
    return transcribe_recording_file(
        recording_file=newest,
        transcript_dir=transcript_dir,
        model_name=model_name,
        device=device,
        compute_type=compute_type,
        language=language,
        beam_size=beam_size,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Transcribe newest recording with Faster-Whisper")
    parser.add_argument("--recordings-dir", default="recordings", help="Directory containing recording MP3 files")
    parser.add_argument("--transcript-dir", default="whisper_transcript", help="Directory for transcript text output")
    parser.add_argument("--model", default="small", help="Whisper model name/path")
    parser.add_argument("--device", default="cuda", help="Inference device (cuda/cpu)")
    parser.add_argument("--compute-type", default="float16", help="CTranslate2 compute type")
    parser.add_argument("--language", default="en", help="Language hint (empty for auto)")
    parser.add_argument("--beam-size", type=int, default=5, help="Beam size")
    parser.add_argument(
        "--fallback-to-cpu",
        default=os.getenv("WHISPER_FALLBACK_TO_CPU", "false"),
        help="Whether to fallback to CPU if CUDA runtime is unavailable (default: false)",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    os.environ["WHISPER_FALLBACK_TO_CPU"] = str(args.fallback_to_cpu)
    output = transcribe_newest_recording(
        recordings_dir=Path(args.recordings_dir),
        transcript_dir=Path(args.transcript_dir),
        model_name=args.model,
        device=args.device,
        compute_type=args.compute_type,
        language=args.language,
        beam_size=args.beam_size,
    )

    if output is None:
        print("No recording found to transcribe.")
        return 0

    print(f"Transcript saved: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
