import unittest
from unittest.mock import patch
import tempfile
import time
from pathlib import Path

from normalize_transcript_with_gemini import (
    build_conversation_pair_block,
    build_prompt,
    ensure_outcome_first_line,
    extract_agent_reference_lines,
    extract_call_sid_from_name,
    get_normalizer_api_key,
    merge_consecutive_speaker_tags,
    resolve_conversation_file_for_input,
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

    def test_ensure_outcome_first_line_keeps_valid_label(self) -> None:
        raw = "outcome=completed\n[AGENT]: Hi\n[USER]: Hello"
        self.assertEqual(ensure_outcome_first_line(raw), raw)

    def test_ensure_outcome_first_line_inserts_default_when_missing(self) -> None:
        raw = "[AGENT]: Hi\n[USER]: Hello"
        self.assertEqual(
            ensure_outcome_first_line(raw),
            "outcome=incomplete\n[AGENT]: Hi\n[USER]: Hello",
        )

    def test_ensure_outcome_first_line_removes_invalid_label(self) -> None:
        raw = "outcome=unknown\n[AGENT]: Hi"
        self.assertEqual(
            ensure_outcome_first_line(raw),
            "outcome=incomplete\noutcome=unknown\n[AGENT]: Hi",
        )

    def test_extract_agent_reference_lines(self) -> None:
        raw = "\n".join(
            [
                "[0.00-2.20] agent> Hello there",
                "user> Hi",
                "[4.00-5.00] agent> What's your current weight in pounds?",
                "[5.00-6.00] user> 140",
            ]
        )
        pairs = extract_agent_reference_lines(raw)
        self.assertEqual(
            pairs,
            [
                "agent> Hello there",
                "agent> What's your current weight in pounds?",
            ],
        )

    def test_extract_call_sid_from_name(self) -> None:
        name = "20260329T101844Z_CAdc51095739fa6b068aeabd26505e95a9_REabc.txt"
        self.assertEqual(
            extract_call_sid_from_name(name),
            "CAdc51095739fa6b068aeabd26505e95a9",
        )

    def test_resolve_conversation_file_for_input(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            conv = d / "20260329T101352Z_CAdc51095739fa6b068aeabd26505e95a9.txt"
            conv.write_text("x", encoding="utf-8")

            input_file = d / "20260329T101844Z_CAdc51095739fa6b068aeabd26505e95a9_REabc.txt"
            input_file.write_text("y", encoding="utf-8")

            resolved = resolve_conversation_file_for_input(input_file, d)
            self.assertEqual(resolved, conv)

    def test_build_conversation_pair_block_no_file(self) -> None:
        self.assertEqual(
            build_conversation_pair_block(None),
            "- (no matching conversation file found)",
        )

    def test_build_prompt_includes_reference_block(self) -> None:
        prompt = build_prompt("[AGENT]: Hi", "- agent> Q")
        self.assertIn("- agent> Q", prompt)



if __name__ == "__main__":
    unittest.main()
