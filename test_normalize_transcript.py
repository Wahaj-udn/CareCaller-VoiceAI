import unittest
from unittest.mock import patch
import tempfile
import time
from pathlib import Path

from normalize_transcript_with_gemini import (
    get_normalizer_api_key,
    merge_consecutive_speaker_tags,
    resolve_input_file,
    sanitize_model_output,
)


class NormalizeTranscriptTests(unittest.TestCase):
    def test_sanitize_model_output_removes_code_fence(self) -> None:
        raw = "```text\n[AGENT]: Hi\n[USER]: Hello\n```"
        cleaned = sanitize_model_output(raw)
        self.assertEqual(cleaned, "[AGENT]: Hi\n[USER]: Hello")

    def test_get_normalizer_api_key_prefers_dedicated_env(self) -> None:
        with patch.dict(
            "os.environ",
            {
                "TRANSCRIPT_NORMALIZER_GEMINI_API_KEY": "dedicated-key",
                "GEMINI_API_KEY": "fallback-key",
            },
            clear=False,
        ):
            self.assertEqual(get_normalizer_api_key(), "dedicated-key")

    def test_get_normalizer_api_key_falls_back_to_gemini_api_key(self) -> None:
        with patch.dict(
            "os.environ",
            {
                "TRANSCRIPT_NORMALIZER_GEMINI_API_KEY": "",
                "GEMINI_API_KEY": "fallback-key",
            },
            clear=False,
        ):
            self.assertEqual(get_normalizer_api_key(), "fallback-key")

    def test_resolve_input_file_prefers_explicit_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sample.txt"
            path.write_text("x", encoding="utf-8")
            resolved = resolve_input_file(str(path), str(Path(tmp)))
            self.assertEqual(resolved, path.resolve())

    def test_resolve_input_file_uses_latest_from_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            older = d / "a.txt"
            newer = d / "b.txt"
            older.write_text("old", encoding="utf-8")
            time.sleep(0.01)
            newer.write_text("new", encoding="utf-8")

            resolved = resolve_input_file("", str(d))
            self.assertEqual(resolved, newer.resolve())

    def test_merge_consecutive_speaker_tags(self) -> None:
        raw = "\n".join(
            [
                "[AGENT]: Hello",
                "[AGENT]: How are you?",
                "[USER]: Fine",
                "[USER]: thanks",
                "[AGENT]: Great",
            ]
        )
        merged = merge_consecutive_speaker_tags(raw)
        self.assertEqual(
            merged,
            "\n".join(
                [
                    "[AGENT]: Hello How are you?",
                    "[USER]: Fine thanks",
                    "[AGENT]: Great",
                ]
            ),
        )


if __name__ == "__main__":
    unittest.main()
