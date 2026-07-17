import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent


class WebNoOverlayConfirmationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = (ROOT / "web" / "app.js").read_text(encoding="utf-8")

    def test_single_download_requires_confirmation_without_overlay(self) -> None:
        self.assertIn("function confirmDownloadWithoutOverlay(videoCount = 1)", self.source)
        self.assertIn(
            "Вы не добавили логотип. Скачать ${subject} без оверлея?",
            self.source,
        )
        handler = self.source[self.source.index("async function startDownloadUrl("):]
        confirmation = handler.index("!confirmDownloadWithoutOverlay()")
        request = handler.index("api('/api/videos/download'")
        self.assertLess(confirmation, request)
        self.assertIn("return false;", handler[confirmation:request])

    def test_batch_asks_once_before_disabling_controls(self) -> None:
        handler = self.source[
            self.source.index("async function submitVideoBatch(records)") :
            self.source.index("function currentDownloadSettings()")
        ]
        confirmation = handler.index("confirmDownloadWithoutOverlay(records.length)")
        running = handler.index("state.batchRunning = true")
        request = handler.index("api('/api/videos/download/batch'")
        self.assertLess(confirmation, running)
        self.assertLess(running, request)
        self.assertEqual(
            handler.count("confirmDownloadWithoutOverlay(records.length)"),
            1,
        )


if __name__ == "__main__":
    unittest.main()
