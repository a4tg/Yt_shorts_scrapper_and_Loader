import json
import unittest

from server_core import normalize_channel_shorts_url, normalize_video_url, parse_metadata_lines


class ServerUrlTests(unittest.TestCase):
    def test_channel_url(self) -> None:
        self.assertEqual(
            normalize_channel_shorts_url("youtube.com/@demo/videos"),
            "https://www.youtube.com/@demo/shorts",
        )

    def test_short_url(self) -> None:
        self.assertEqual(
            normalize_video_url("https://www.youtube.com/shorts/abcdefghijk"),
            "https://www.youtube.com/shorts/abcdefghijk",
        )

    def test_rejects_watch_url(self) -> None:
        with self.assertRaises(ValueError):
            normalize_video_url("https://www.youtube.com/watch?v=abcdefghijk")


class MetadataParserTests(unittest.TestCase):
    def test_parses_compact_json(self) -> None:
        payload = {
            "id": "abcdefghijk",
            "title": "Demo",
            "description": "Text",
            "tags": ["one"],
            "duration": 42,
            "thumbnail": "https://example.test/image.jpg",
        }
        records = parse_metadata_lines(json.dumps(payload) + "\nnot-json")
        self.assertEqual(records[0]["url"], "https://www.youtube.com/shorts/abcdefghijk")
        self.assertEqual(records[0]["tags"], ["one"])
        self.assertEqual(records[0]["duration"], 42)


if __name__ == "__main__":
    unittest.main()
