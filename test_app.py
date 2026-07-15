import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from app import (
    build_logo_filter,
    build_overlay_input_args,
    build_variant_directories,
    create_logo_variants,
    create_logo_variants_batch,
    extract_video_ids,
    extract_video_id_from_url,
    find_downloaded_video,
    is_paste_shortcut,
    is_youtube_rate_limit_message,
    youtube_rate_limit_wait_seconds,
    normalize_channel_shorts_url,
    is_supported_overlay,
)
from server_core import build_overlay_filter as build_server_overlay_filter


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

    def test_extracts_id_from_shorts_url(self) -> None:
        self.assertEqual(
            extract_video_id_from_url("https://www.youtube.com/shorts/H5OA7vV56HQ"),
            "H5OA7vV56HQ",
        )

    def test_recovers_real_unicode_filename_by_video_id(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            actual = output_dir / "Семейка 🎬 [H5OA7vV56HQ].mp4"
            actual.write_bytes(b"video")
            corrupted = output_dir / "������� [H5OA7vV56HQ].mp4"

            found = find_downloaded_video(
                output_dir,
                corrupted,
                "https://www.youtube.com/shorts/H5OA7vV56HQ",
                started_at=actual.stat().st_mtime,
            )

        self.assertEqual(found, actual)


class PasteShortcutTests(unittest.TestCase):
    def test_ctrl_v_is_recognized_in_english_layout(self) -> None:
        self.assertTrue(is_paste_shortcut("v", 86))

    def test_physical_v_key_is_recognized_in_russian_layout(self) -> None:
        self.assertTrue(is_paste_shortcut("Cyrillic_em", 86))

    def test_other_control_key_is_not_paste(self) -> None:
        self.assertFalse(is_paste_shortcut("c", 67))


class YoutubeRateLimitTests(unittest.TestCase):
    def test_detects_rate_limit_error(self) -> None:
        self.assertTrue(
            is_youtube_rate_limit_message(
                "The current session has been rate-limited by YouTube for up to an hour"
            )
        )

    def test_does_not_treat_regular_unavailable_video_as_rate_limit(self) -> None:
        self.assertFalse(is_youtube_rate_limit_message("Video unavailable: private video"))

    def test_parses_up_to_an_hour(self) -> None:
        self.assertEqual(
            youtube_rate_limit_wait_seconds(
                "The current session has been rate-limited by YouTube for up to an hour"
            ),
            3600,
        )

    def test_parses_numeric_minutes(self) -> None:
        self.assertEqual(
            youtube_rate_limit_wait_seconds(
                "The current session has been rate-limited by YouTube for 30 minutes"
            ),
            1800,
        )

    def test_returns_none_for_unrelated_error(self) -> None:
        self.assertIsNone(youtube_rate_limit_wait_seconds("HTTP Error 404"))


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
        self.assertIn("overlay=x=(W-w)*0.50:y=(H-h)*0.96", overlay_filter)
        self.assertIn("shortest=1", overlay_filter)

    def test_builds_overlay_filter_from_constructor_position(self) -> None:
        overlay_filter = build_logo_filter(
            1000, opacity=60, width_percent=40, position_x=15, position_y=70,
        )

        self.assertIn("scale=400:-1", overlay_filter)
        self.assertIn("overlay=x=(W-w)*0.15:y=(H-h)*0.70", overlay_filter)

    def test_desktop_and_web_build_the_same_overlay_filter(self) -> None:
        self.assertEqual(
            build_logo_filter(1080, 45, 31, position_x=12, position_y=78),
            build_server_overlay_filter(335, 45, position_x=12, position_y=78),
        )

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

    def test_variants_forward_constructor_position(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_path = root / "source.mp4"
            source_path.write_bytes(b"video")
            logo_path = root / "banner.png"
            logo_path.write_bytes(b"png")

            with patch("app.overlay_logo", return_value=True) as overlay_mock:
                create_logo_variants(
                    source_path,
                    root / "result",
                    [logo_path],
                    opacity=45,
                    width_percent=35,
                    log=lambda _message: None,
                    position_x=12,
                    position_y=78,
                )

        self.assertEqual(overlay_mock.call_args.args[-2:], (12, 78))

    def test_batch_deletes_only_fully_processed_sources(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            complete_source = root / "complete.mp4"
            partial_source = root / "partial.mp4"
            complete_source.write_bytes(b"complete")
            partial_source.write_bytes(b"partial")
            logo_paths = [root / "first.gif", root / "second.gif"]
            progress_calls: list[tuple[int, int]] = []

            with patch(
                "app.create_logo_variants",
                side_effect=[
                    [root / "first" / "complete.mp4", root / "second" / "complete.mp4"],
                    [root / "first" / "partial.mp4"],
                ],
            ):
                processed = create_logo_variants_batch(
                    [complete_source, partial_source],
                    root,
                    logo_paths,
                    opacity=35,
                    width_percent=22,
                    log=lambda _message: None,
                    progress=lambda current, total: progress_calls.append((current, total)),
                )

            self.assertEqual(processed, 1)
            self.assertFalse(complete_source.exists())
            self.assertTrue(partial_source.exists())
            self.assertEqual(progress_calls, [(1, 2), (2, 2)])


if __name__ == "__main__":
    unittest.main()
