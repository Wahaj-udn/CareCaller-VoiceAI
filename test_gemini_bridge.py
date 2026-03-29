import base64
import unittest

from gemini_bridge import (
    _format_seconds_compact,
    _pcm_audio_duration_seconds,
    _parse_sample_rate,
    pcm16_rms,
    pcm_to_twilio_payload,
    twilio_payload_to_pcm16_16k,
)


class GeminiBridgeCodecTests(unittest.TestCase):
    def test_sample_rate_parse(self) -> None:
        self.assertEqual(_parse_sample_rate("audio/pcm;rate=24000", 16000), 24000)
        self.assertEqual(_parse_sample_rate("audio/pcm", 16000), 16000)
        self.assertEqual(_parse_sample_rate(None, 8000), 8000)

    def test_twilio_to_pcm16k_has_expected_size(self) -> None:
        # 20ms of 8kHz mu-law = 160 bytes.
        ulaw = bytes([255] * 160)
        payload = base64.b64encode(ulaw).decode("ascii")
        pcm16k = twilio_payload_to_pcm16_16k(payload)
        # 20ms of 16kHz 16-bit PCM is ~640 bytes; resampling can differ by a few bytes.
        self.assertLessEqual(abs(len(pcm16k) - 640), 4)

    def test_pcm_to_twilio_payload_roundtrip_length(self) -> None:
        # 20ms of 24kHz PCM16 => 480 samples -> should become 160 bytes mu-law at 8kHz.
        pcm24k = b"\x00\x00" * 480
        payload = pcm_to_twilio_payload(pcm24k, input_rate=24000)
        ulaw = base64.b64decode(payload)
        self.assertEqual(len(ulaw), 160)

    def test_pcm16_rms(self) -> None:
        silent = b"\x00\x00" * 100
        self.assertEqual(pcm16_rms(silent), 0)

    def test_pcm_audio_duration_seconds(self) -> None:
        # 320 samples @16kHz => 0.02s. Each sample is 2 bytes.
        pcm = b"\x00\x00" * 320
        self.assertAlmostEqual(_pcm_audio_duration_seconds(pcm, 16000), 0.02, places=6)

    def test_format_seconds_compact(self) -> None:
        self.assertEqual(_format_seconds_compact(0.0), "0")
        self.assertEqual(_format_seconds_compact(12.300), "12.3")
        self.assertEqual(_format_seconds_compact(12.3456), "12.346")


if __name__ == "__main__":
    unittest.main()
