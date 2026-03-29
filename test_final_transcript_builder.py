import tempfile
import unittest
from pathlib import Path

from final_transcript_builder import build_for_whisper_file, _is_agent_filler_phrase


class FinalTranscriptBuilderTests(unittest.TestCase):
    def test_agent_filler_phrase_rule(self) -> None:
        self.assertTrue(_is_agent_filler_phrase("OK"))
        self.assertTrue(_is_agent_filler_phrase("Got it"))
        self.assertTrue(_is_agent_filler_phrase("Thanks"))
        self.assertTrue(_is_agent_filler_phrase("That's good to hear"))
        self.assertFalse(_is_agent_filler_phrase("I think 145 pounds"))

    def test_build_for_whisper_file_labels_and_writes_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            whisper_dir = root / "whisper_transcript"
            conversation_dir = root / "conversation"
            output_dir = root / "final_transcript"
            whisper_dir.mkdir(parents=True, exist_ok=True)
            conversation_dir.mkdir(parents=True, exist_ok=True)

            call_sid = "CA2109d0cba7a91602b6e4b55f79014d43"
            whisper_file = whisper_dir / f"20260328T193429Z_{call_sid}_RE123.txt"
            whisper_file.write_text(
                "\n".join(
                    [
                        f"source={whisper_file.name.replace('.txt', '.mp3')}",
                        "language=en",
                        "duration=10.0",
                        "",
                        "[00:00:01.000 -> 00:00:03.000] Hi, is this Wahaj?",
                        "[00:00:03.000 -> 00:00:05.000] Yes, this is Wahaj.",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            conv_file = conversation_dir / f"20260328T193144Z_{call_sid}.txt"
            conv_file.write_text(
                "\n".join(
                    [
                        f"# call_sid={call_sid}",
                        "[1-3] agent>Hi, is this Wahaj?",
                        "user>Yes, this is Wahaj.",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            output = build_for_whisper_file(
                whisper_file=whisper_file,
                conversation_dir=conversation_dir,
                output_dir=output_dir,
            )

            self.assertIsNotNone(output)
            assert output is not None
            self.assertTrue(output.exists())
            content = output.read_text(encoding="utf-8")
            self.assertIn("conversation_source=", content)
            self.assertIn("[1-3] agent>", content)
            self.assertIn("[3-5] user>", content)

    def test_merges_consecutive_same_speaker_lines(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            whisper_dir = root / "whisper_transcript"
            conversation_dir = root / "conversation"
            output_dir = root / "final_transcript"
            whisper_dir.mkdir(parents=True, exist_ok=True)
            conversation_dir.mkdir(parents=True, exist_ok=True)

            call_sid = "CA2109d0cba7a91602b6e4b55f79014d43"
            whisper_file = whisper_dir / f"20260328T193429Z_{call_sid}_RE999.txt"
            whisper_file.write_text(
                "\n".join(
                    [
                        f"source={whisper_file.name.replace('.txt', '.mp3')}",
                        "language=en",
                        "duration=12.0",
                        "",
                        "[00:00:01.000 -> 00:00:03.000] Hello there",
                        "[00:00:03.000 -> 00:00:05.000] how are you",
                        "[00:00:05.000 -> 00:00:07.000] I am fine",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            conv_file = conversation_dir / f"20260328T193144Z_{call_sid}.txt"
            conv_file.write_text(
                "\n".join(
                    [
                        f"# call_sid={call_sid}",
                        "[1-5] agent>Hello there how are you",
                        "user>I am fine",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            output = build_for_whisper_file(
                whisper_file=whisper_file,
                conversation_dir=conversation_dir,
                output_dir=output_dir,
            )

            self.assertIsNotNone(output)
            assert output is not None
            content = output.read_text(encoding="utf-8")
            self.assertIn("[1-5] agent> Hello there how are you", content)
            self.assertIn("[5-7] user> I am fine", content)


if __name__ == "__main__":
    unittest.main()
