import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import Mock, patch

from server_core import (
    build_overlay_filter,
    build_overlay_input_args,
    create_overlay_archive,
    find_downloaded_video,
    is_supported_overlay,
    normalize_channel_shorts_url,
    normalize_video_url,
    parse_metadata_lines,
    playlist_limit_args,
)


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


class OverlayMediaTests(unittest.TestCase):
    def test_creates_one_zip_folder_per_overlay(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source.mp4"
            source.write_bytes(b"video")
            overlays = [(root / "one.png", "promo.png"), (root / "two.gif", "promo.gif")]
            for overlay_path, _name in overlays:
                overlay_path.write_bytes(b"overlay")
            archive_path = root / "variants.zip"

            with patch("server_core.overlay_logo_cpu") as overlay_mock:
                folders = create_overlay_archive(
                    source, overlays, archive_path,
                    opacity=35, width_percent=22, position_x=50, position_y=96,
                    log=lambda _message: None,
                )

            with zipfile.ZipFile(archive_path) as archive:
                names = archive.namelist()

        self.assertEqual(folders, ["promo", "promo_2"])
        self.assertEqual(names, ["promo/source.mp4", "promo_2/source.mp4"])
        self.assertEqual(overlay_mock.call_count, 2)

    def test_overlay_filter_uses_constructor_position(self) -> None:
        overlay_filter = build_overlay_filter(240, 35, position_x=75, position_y=20)

        self.assertIn("scale=240:-1", overlay_filter)
        self.assertIn("colorchannelmixer=aa=0.35", overlay_filter)
        self.assertIn("overlay=x=(W-w)*0.75:y=(H-h)*0.20", overlay_filter)

    def test_video_overlay_is_looped(self) -> None:
        self.assertEqual(
            build_overlay_input_args(Path("overlay.mov")),
            ["-stream_loop", "-1", "-i", "overlay.mov"],
        )

    def test_static_image_is_held(self) -> None:
        self.assertEqual(
            build_overlay_input_args(Path("overlay.jpg")),
            ["-loop", "1", "-i", "overlay.jpg"],
        )

    def test_ffprobe_validation_accepts_visual_stream(self) -> None:
        with patch("server_core.resolve_tool", return_value="ffprobe"), patch(
            "server_core.subprocess.run",
            return_value=Mock(returncode=0, stdout="video\n"),
        ):
            self.assertTrue(is_supported_overlay(Path("overlay.anything")))


if __name__ == "__main__":
    unittest.main()
