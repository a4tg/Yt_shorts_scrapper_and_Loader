import csv
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from app import (
    build_logo_filter,
    build_overlay_input_args,
    build_variant_directories,
    create_logo_variants,
    extract_video_ids,
    normalize_channel_shorts_url,
    parse_shorts_metadata,
    is_supported_overlay,
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
    def test_loops_static_images_and_animated_media_correctly(self) -> None:
        self.assertEqual(
            build_overlay_input_args(Path("logo.png")),
            ["-loop", "1", "-i", "logo.png"],
        )
        self.assertEqual(
            build_overlay_input_args(Path("animation.mov")),
            ["-stream_loop", "-1", "-i", "animation.mov"],
        )
        self.assertEqual(
            build_overlay_input_args(Path("animation.webm")),
            ["-stream_loop", "-1", "-i", "animation.webm"],
        )

    def test_accepts_any_file_with_a_visual_stream(self) -> None:
        with patch(
            "app.subprocess.run",
            return_value=Mock(returncode=0, stdout="video\n"),
        ):
            self.assertTrue(is_supported_overlay(Path("animation.custom"), "ffprobe"))

    def test_builds_centered_bottom_overlay_filter(self) -> None:
        overlay_filter = build_logo_filter(1080, opacity=35, width_percent=22)

        self.assertIn("scale=238:-1", overlay_filter)
        self.assertIn("colorchannelmixer=aa=0.35", overlay_filter)
        self.assertIn("overlay=(W-w)/2:H-h-H*0.03", overlay_filter)
        self.assertIn("shortest=1", overlay_filter)

    def test_clamps_opacity(self) -> None:
        self.assertIn("aa=1.00", build_logo_filter(1000, opacity=200, width_percent=20))

    def test_builds_unique_safe_directories_from_logo_names(self) -> None:
        output_dir = Path("C:/result")
        directories = build_variant_directories(
            output_dir,
            [Path("C:/one/promo.gif"), Path("C:/two/promo.gif"), Path("C:/CON.gif")],
        )

        self.assertEqual(
            directories,
            [output_dir / "promo", output_dir / "promo_2", output_dir / "CON_gif"],
        )

    def test_creates_one_video_copy_per_logo(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_path = root / "source.mp4"
            source_path.write_bytes(b"video")
            logo_paths = [root / "first.gif", root / "second.gif"]
            for logo_path in logo_paths:
                logo_path.write_bytes(b"gif")

            with patch("app.overlay_logo", return_value=True) as overlay_mock:
                completed = create_logo_variants(
                    source_path,
                    root / "result",
                    logo_paths,
                    opacity=35,
                    width_percent=22,
                    log=lambda _message: None,
                )

            self.assertEqual(
                completed,
                [root / "result" / "first" / "source.mp4", root / "result" / "second" / "source.mp4"],
            )
            self.assertTrue(all(path.read_bytes() == b"video" for path in completed))
            self.assertEqual(overlay_mock.call_count, 2)


if __name__ == "__main__":
    unittest.main()
