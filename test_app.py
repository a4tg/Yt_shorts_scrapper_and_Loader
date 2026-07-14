import csv
import tempfile
import unittest
from pathlib import Path

from app import (
    build_logo_filter,
    extract_video_ids,
    normalize_channel_shorts_url,
    parse_shorts_metadata,
    write_shorts_metadata_csv,
)


class NormalizeChannelShortsUrlTests(unittest.TestCase):
    def test_handle_url(self) -> None:
        self.assertEqual(
            normalize_channel_shorts_url("https://www.youtube.com/@example"),
            "https://www.youtube.com/@example/shorts",
        )

    def test_existing_tab_and_query_are_replaced(self) -> None:
        self.assertEqual(
            normalize_channel_shorts_url("youtube.com/@example/videos?view=0"),
            "https://www.youtube.com/@example/shorts",
        )

    def test_channel_id_url(self) -> None:
        self.assertEqual(
            normalize_channel_shorts_url("https://m.youtube.com/channel/UC123/shorts"),
            "https://www.youtube.com/channel/UC123/shorts",
        )

    def test_video_url_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            normalize_channel_shorts_url("https://www.youtube.com/watch?v=abcdefghijk")

    def test_non_youtube_url_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            normalize_channel_shorts_url("https://example.com/@channel")


class ExtractVideoIdsTests(unittest.TestCase):
    def test_filters_invalid_lines_and_duplicates(self) -> None:
        output = "abcdefghijk\nwarning text\n123456789_-\nabcdefghijk\n"
        self.assertEqual(extract_video_ids(output), ["abcdefghijk", "123456789_-"])


class ShortsMetadataTests(unittest.TestCase):
    def test_parses_metadata_and_skips_noise_and_duplicates(self) -> None:
        output = (
            'not json\n'
            '{"id":"abcdefghijk","title":"Title","description":"Line 1\\nLine 2",'
            '"tags":["one","two"],"uploader":"Channel","upload_date":"20260714"}\n'
            '{"id":"abcdefghijk","title":"Duplicate"}\n'
        )

        self.assertEqual(
            parse_shorts_metadata(output),
            [
                {
                    "id": "abcdefghijk",
                    "url": "https://www.youtube.com/shorts/abcdefghijk",
                    "title": "Title",
                    "description": "Line 1\nLine 2",
                    "tags": ["one", "two"],
                    "uploader": "Channel",
                    "upload_date": "20260714",
                }
            ],
        )

    def test_writes_excel_friendly_csv(self) -> None:
        record = {
            "id": "abcdefghijk",
            "url": "https://www.youtube.com/shorts/abcdefghijk",
            "title": "Название",
            "description": "Описание",
            "tags": ["тег 1", "тег 2"],
            "uploader": "Канал",
            "upload_date": "20260714",
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "metadata.csv"
            write_shorts_metadata_csv([record], output_path)
            with output_path.open(encoding="utf-8-sig", newline="") as csv_file:
                rows = list(csv.DictReader(csv_file, delimiter=";"))

        self.assertEqual(rows[0]["tags"], "тег 1, тег 2")
        self.assertEqual(rows[0]["description"], "Описание")


class LogoOverlayTests(unittest.TestCase):
    def test_builds_centered_bottom_overlay_filter(self) -> None:
        overlay_filter = build_logo_filter(1080, opacity=35, width_percent=22)

        self.assertIn("scale=238:-1", overlay_filter)
        self.assertIn("colorchannelmixer=aa=0.35", overlay_filter)
        self.assertIn("overlay=(W-w)/2:H-h-H*0.03", overlay_filter)
        self.assertIn("shortest=1", overlay_filter)

    def test_clamps_opacity(self) -> None:
        self.assertIn("aa=1.00", build_logo_filter(1000, opacity=200, width_percent=20))


if __name__ == "__main__":
    unittest.main()
