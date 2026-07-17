import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent


class WebVideoLibraryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
        cls.script = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
        cls.styles = (ROOT / "web" / "video-library.css").read_text(encoding="utf-8")

    def test_video_page_has_profile_library_grouped_by_channel(self) -> None:
        self.assertIn('id="profile-video-library"', self.html)
        self.assertIn("async function loadVideoLibrary()", self.script)
        self.assertIn("api('/api/videos/library')", self.script)
        self.assertIn("job.channel_name || 'Без канала'", self.script)
        self.assertIn("video-library-folder", self.styles)

    def test_download_payload_preserves_channel_and_video_title(self) -> None:
        self.assertIn("channel_name: source.channelName || null", self.script)
        self.assertIn("video_title: source.videoTitle || null", self.script)
        self.assertIn("channelName: record.item.uploader", self.script)

    def test_library_cards_offer_download_action(self) -> None:
        self.assertIn("showReadyDownload(job, downloadButton, note)", self.script)
        self.assertIn("downloadButton.textContent = 'Скачать'", self.script)


if __name__ == "__main__":
    unittest.main()
