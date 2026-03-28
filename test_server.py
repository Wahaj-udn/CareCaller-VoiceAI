import os
import unittest

from server import create_app


class ServerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app = create_app()
        self.client = self.app.test_client()

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


if __name__ == "__main__":
    unittest.main()
