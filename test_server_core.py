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
    normalize_source_import_url,
    normalize_source_video_url,
    normalize_video_url,
    overlay_preview_seek_seconds,
    merge_import_records,
    parse_metadata_lines,
    playlist_limit_args,
    run_source_import,
)
from media_metadata import metadata_movflags, metadata_output_args, normalize_metadata_mode


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

    def test_supported_source_channels_are_normalized(self) -> None:
        self.assertEqual(
            normalize_source_import_url("https://youtube.com/@demo", "auto"),
            ("https://www.youtube.com/@demo/shorts", "youtube"),
        )
        self.assertEqual(
            normalize_source_import_url("https://vk.com/video/@mobidevices", "vk"),
            ("https://vk.com/video/@mobidevices", "vk"),
        )
        self.assertEqual(
            normalize_source_import_url("https://rutube.ru/channel/25902603/shorts/", "rutube"),
            ("https://rutube.ru/channel/25902603/shorts/", "rutube"),
        )

    def test_supported_direct_video_urls_are_validated(self) -> None:
        self.assertEqual(
            normalize_source_video_url("https://vk.com/video-77521_162222515")[1], "vk"
        )
        self.assertEqual(
            normalize_source_video_url(
                "https://rutube.ru/video/0123456789abcdef0123456789abcdef/"
            )[1],
            "rutube",
        )
        with self.assertRaises(ValueError):
            normalize_source_video_url("https://example.com/video/123")
        with self.assertRaises(ValueError):
            normalize_source_import_url("http://vk.com/video/@demo")
        with self.assertRaises(ValueError):
            normalize_source_import_url("https://vk.com/video/@demo", "rutube")


class OverlayPreviewTests(unittest.TestCase):
    def test_preview_uses_twenty_percent_frame(self) -> None:
        self.assertEqual(overlay_preview_seek_seconds(10.0), 2.0)
        self.assertEqual(overlay_preview_seek_seconds(0.0), 0.0)
        self.assertAlmostEqual(overlay_preview_seek_seconds(0.1), 0.02)


class MetadataParserTests(unittest.TestCase):
    def test_detailed_metadata_replaces_fast_record_without_changing_order(self) -> None:
        preliminary = [
            {"id": "first", "title": "Fast first"},
            {"id": "second", "title": "Fast second"},
        ]
        detailed = [
            {"id": "second", "title": "Detailed second", "view_count": 25},
        ]
        merged = merge_import_records(preliminary, detailed)
        self.assertEqual([record["id"] for record in merged], ["first", "second"])
        self.assertEqual(merged[1]["view_count"], 25)

    def test_parses_compact_json(self) -> None:
        payload = {
            "id": "abcdefghijk",
            "title": "Demo",
            "description": "Text",
            "tags": ["one"],
            "upload_date": "20260717",
            "view_count": 125400,
            "duration": 42,
            "thumbnail": "https://example.test/image.jpg",
        }
        records = parse_metadata_lines(json.dumps(payload) + "\nnot-json")
        self.assertEqual(records[0]["url"], "https://www.youtube.com/shorts/abcdefghijk")
        self.assertEqual(records[0]["tags"], ["one"])
        self.assertEqual(records[0]["published_at"], "2026-07-17")
        self.assertEqual(records[0]["view_count"], 125400)
        self.assertEqual(records[0]["duration"], 42)

    def test_publication_date_falls_back_to_timestamp(self) -> None:
        records = parse_metadata_lines(json.dumps({
            "id": "abcdefghijk",
            "timestamp": 1767225600,
            "view_count": "42",
        }))
        self.assertEqual(records[0]["published_at"], "2026-01-01")
        self.assertEqual(records[0]["view_count"], 42)

    def test_parses_vk_and_rutube_metadata_without_youtube_id_rules(self) -> None:
        vk = parse_metadata_lines(json.dumps({
            "id": "-77521_162222515", "title": "VK demo",
            "webpage_url": "https://vk.com/video-77521_162222515",
        }), "vk")[0]
        rutube = parse_metadata_lines(json.dumps({
            "id": "0123456789abcdef0123456789abcdef", "title": "Rutube demo",
        }), "rutube")[0]
        self.assertEqual(vk["platform"], "vk")
        self.assertEqual(vk["url"], "https://vk.com/video-77521_162222515")
        self.assertEqual(rutube["platform"], "rutube")
        self.assertIn(rutube["id"], rutube["url"])

    def test_vk_import_writes_platform_metadata_and_uses_playlist_limit(self) -> None:
        payload = json.dumps({
            "id": "-77521_162222515",
            "webpage_url": "https://vk.com/video-77521_162222515",
            "title": "VK demo",
            "tags": ["one"],
        })
        with tempfile.TemporaryDirectory() as temp_dir, patch(
            "server_core.resolve_tool", return_value="yt-dlp"
        ), patch(
            "server_core.subprocess.run", return_value=Mock(returncode=0, stdout=payload, stderr="")
        ) as execute:
            root = Path(temp_dir)
            count, platform = run_source_import(
                "https://vk.com/video/@mobidevices",
                root / "items.json",
                root / "items.csv",
                limit=25,
                platform="auto",
            )
            saved = json.loads((root / "items.json").read_text(encoding="utf-8"))
            csv_text = (root / "items.csv").read_text(encoding="utf-8-sig")
        commands = [call.args[0] for call in execute.call_args_list]
        self.assertEqual((count, platform), (1, "vk"))
        self.assertEqual(len(commands), 2)
        self.assertIn("--flat-playlist", commands[0])
        self.assertIn("--lazy-playlist", commands[0])
        self.assertIn("--playlist-end", commands[1])
        self.assertIn("25", commands[1])
        self.assertIn("view_count", " ".join(commands[1]))
        self.assertEqual(saved[0]["platform"], "vk")
        self.assertIn("platform;url;title", csv_text)
        self.assertIn("published_at", csv_text)
        self.assertIn("view_count", csv_text)


class MetadataModeTests(unittest.TestCase):
    def test_standard_mode_strips_input_metadata(self) -> None:
        args = metadata_output_args("strip", "video.mp4")

        self.assertIn("-map_metadata", args)
        self.assertIn("-map_metadata:s", args)
        self.assertIn("-map_chapters", args)
        self.assertIn("+bitexact", args)
        self.assertNotIn("make=Apple", args)
        self.assertEqual(metadata_movflags("strip"), "+faststart")

    def test_synthetic_mode_replaces_device_fields_stably(self) -> None:
        first = metadata_output_args("synthetic", "video.mp4")
        second = metadata_output_args("synthetic", "video.mp4")

        self.assertEqual(first, second)
        self.assertIn("make=Apple", first)
        self.assertTrue(any(value.startswith("com.apple.quicktime.model=") for value in first))
        self.assertEqual(metadata_movflags("synthetic"), "+faststart+use_metadata_tags")

    def test_unknown_mode_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            normalize_metadata_mode("magic")


class DownloadResultTests(unittest.TestCase):
    def test_finds_file_by_video_id_when_reported_unicode_path_differs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            actual = output_dir / "Русское ｜ название [abcdefghijk].mp4"
            actual.write_bytes(b"video")
            reported = output_dir / "Русское   название [abcdefghijk].mp4"

            found = find_downloaded_video(output_dir, reported, "abcdefghijk")

        self.assertEqual(found, actual)

    def test_finds_only_mp4_for_non_youtube_sources(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            actual = output_dir / "VK video.mp4"
            actual.write_bytes(b"video")
            found = find_downloaded_video(output_dir, None, "")
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
