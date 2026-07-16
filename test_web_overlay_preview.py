import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent


class WebOverlayPreviewTests(unittest.TestCase):
    def test_constructor_contains_source_video_frame(self) -> None:
        html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
        script = (ROOT / "web" / "app.js").read_text(encoding="utf-8")

        self.assertIn('id="stage-video-preview"', html)
        self.assertIn('id="stage-video-player"', html)
        self.assertIn("showSourceVideo(item.url, item.thumbnail, item.title)", script)
        self.assertIn("showExternalSourcePreview($('#direct-video-url').value)", script)
        self.assertIn("https://www.youtube-nocookie.com/embed/", script)

    def test_unsupported_browser_codec_uses_server_preview(self) -> None:
        script = (ROOT / "web" / "app.js").read_text(encoding="utf-8")

        self.assertIn("showGeneratedOverlayPreview(file, expectedIndex)", script)
        self.assertIn("result.preview_url", script)
        self.assertIn("state.logoUploads", script)

    def test_overlay_can_fill_the_entire_video_width(self) -> None:
        html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
        script = (ROOT / "web" / "app.js").read_text(encoding="utf-8")

        self.assertIn('id="logo-width" type="range" min="5" max="100"', html)
        self.assertIn("5, 100", script)


if __name__ == "__main__":
    unittest.main()
