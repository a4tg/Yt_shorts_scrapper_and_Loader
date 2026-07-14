import json
import tempfile
import unittest
from pathlib import Path

from server_core import find_downloaded_video, normalize_channel_shorts_url, normalize_video_url, parse_metadata_lines, playlist_limit_args


class ServerUrlTests(unittest.TestCase):
    def test_channel_url(self) -> None:
        self.assertEqual(
            normalize_channel_shorts_url("youtube.com/@demo/videos"),
            "https://www.youtube.com/@demo/shorts",
        )

    def test_short_url(self) -> None:
        self.assertEqual(
            normalize_video_url("https://www.youtube.com/shorts/abcdefghijk"),
            "https://www.youtube.com/watch?v=abcdefghijk",
        )

    def test_watch_url(self) -> None:
        self.assertEqual(
            normalize_video_url("https://www.youtube.com/watch?v=abcdefghijk&t=10"),
            "https://www.youtube.com/watch?v=abcdefghijk",
        )

    def test_short_youtu_be_url(self) -> None:
        self.assertEqual(
            normalize_video_url("youtu.be/abcdefghijk?si=demo"),
            "https://www.youtube.com/watch?v=abcdefghijk",
        )

    def test_playlist_limit(self) -> None:
        self.assertEqual(playlist_limit_args(50), ["--playlist-end", "50"])
        self.assertEqual(playlist_limit_args(0), [])


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


class DownloadResultTests(unittest.TestCase):
    def test_finds_file_by_video_id_when_reported_unicode_path_differs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            actual = output_dir / "Русское ｜ название [abcdefghijk].mp4"
            actual.write_bytes(b"video")
            reported = output_dir / "Русское   название [abcdefghijk].mp4"

            found = find_downloaded_video(output_dir, reported, "abcdefghijk")

        self.assertEqual(found, actual)


if __name__ == "__main__":
    unittest.main()
