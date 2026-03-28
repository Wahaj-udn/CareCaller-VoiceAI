import os
import tempfile
import unittest
from unittest.mock import patch

from server import create_app


class ServerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app = create_app()
        self.client = self.app.test_client()
        os.environ.pop("MEDIA_STREAM_URL", None)
        os.environ.pop("TWILIO_ACCOUNT_SID", None)
        os.environ.pop("TWILIO_AUTH_TOKEN", None)
        os.environ.pop("RECORDINGS_DIR", None)

    def test_health(self) -> None:
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["ok"])

    def test_incoming_voice_without_stream_hangs_up(self) -> None:
        os.environ.pop("MEDIA_STREAM_URL", None)
        response = self.client.post("/voice/incoming")

        self.assertEqual(response.status_code, 200)
        body = response.data.decode("utf-8")
        self.assertIn("Voice stream is not configured yet", body)
        self.assertIn("<Hangup />", body)

    def test_incoming_voice_with_stream_connects(self) -> None:
        os.environ["MEDIA_STREAM_URL"] = "wss://example.com/media"
        response = self.client.post("/voice/incoming")

        self.assertEqual(response.status_code, 200)
        body = response.data.decode("utf-8")
        self.assertIn("<Connect>", body)
        self.assertIn("wss://example.com/media", body)

    def test_recording_event_non_completed_is_ignored(self) -> None:
        with patch("server._download_recording_mp3") as downloader:
            response = self.client.post(
                "/voice/recording",
                data={
                    "CallSid": "CA123",
                    "RecordingSid": "RE123",
                    "RecordingStatus": "in-progress",
                    "RecordingUrl": "https://api.twilio.com/recordings/RE123",
                },
            )

        self.assertEqual(response.status_code, 204)
        downloader.assert_not_called()

    def test_recording_event_completed_downloads_mp3(self) -> None:
        os.environ["TWILIO_ACCOUNT_SID"] = "AC123"
        os.environ["TWILIO_AUTH_TOKEN"] = "secret"

        with tempfile.TemporaryDirectory() as temp_dir:
            os.environ["RECORDINGS_DIR"] = temp_dir

            with patch("server._download_recording_mp3") as downloader:
                response = self.client.post(
                    "/voice/recording",
                    data={
                        "CallSid": "CA_TEST",
                        "RecordingSid": "RE_TEST",
                        "RecordingStatus": "completed",
                        "RecordingUrl": "https://api.twilio.com/recordings/RE_TEST",
                    },
                )

            self.assertEqual(response.status_code, 204)
            downloader.assert_called_once()
            kwargs = downloader.call_args.kwargs
            self.assertEqual(
                kwargs["recording_url"],
                "https://api.twilio.com/recordings/RE_TEST.mp3",
            )
            self.assertEqual(kwargs["account_sid"], "AC123")
            self.assertEqual(kwargs["auth_token"], "secret")
            self.assertTrue(str(kwargs["output_file"]).endswith(".mp3"))

    def test_recording_event_completed_missing_credentials_fails(self) -> None:
        response = self.client.post(
            "/voice/recording",
            data={
                "CallSid": "CA_TEST",
                "RecordingSid": "RE_TEST",
                "RecordingStatus": "completed",
                "RecordingUrl": "https://api.twilio.com/recordings/RE_TEST",
            },
        )

        self.assertEqual(response.status_code, 500)


if __name__ == "__main__":
    unittest.main()
