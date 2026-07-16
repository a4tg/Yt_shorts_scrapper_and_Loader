import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent


class WebSourceImportTests(unittest.TestCase):
    def test_source_selector_and_generic_video_copy_are_present(self) -> None:
        html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
        self.assertIn('id="source-platform"', html)
        self.assertIn('value="youtube"', html)
        self.assertIn('value="vk"', html)
        self.assertIn('value="rutube"', html)
        self.assertIn("YouTube Shorts, VK Video/Clips и каналы Rutube", html)
        self.assertIn("Конкретное видео", html)

    def test_client_uses_unified_source_api_preview_and_content_plan(self) -> None:
        script = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
        self.assertIn("api('/api/sources/import'", script)
        self.assertIn("/api/sources/preview?url=", script)
        self.assertIn("/api/sources/thumbnail?url=", script)
        self.assertIn("source_platform: item.platform", script)
        self.assertIn("В контент-план", script)


if __name__ == "__main__":
    unittest.main()
