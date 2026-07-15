import unittest
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent


class WebBatchUiTests(unittest.TestCase):
    def test_batch_controls_are_present(self) -> None:
        html = (BASE_DIR / "web" / "index.html").read_text(encoding="utf-8")

        self.assertIn('id="batch-toolbar"', html)
        self.assertIn('id="select-all-videos"', html)
        self.assertIn('id="clear-video-selection"', html)
        self.assertIn('id="prepare-selected"', html)
        self.assertIn('id="batch-status"', html)

    def test_selected_videos_are_awaited_sequentially(self) -> None:
        source = (BASE_DIR / "web" / "app.js").read_text(encoding="utf-8")
        handler = source[source.index("$('#prepare-selected').addEventListener"):]
        loop_position = handler.index("for (let index = 0; index < records.length; index += 1)")
        await_position = handler.index("await startDownloadUrl", loop_position)

        self.assertGreater(await_position, loop_position)
        self.assertNotIn("Promise.all", handler[:await_position])

    def test_overlays_are_uploaded_once_before_batch_loop(self) -> None:
        source = (BASE_DIR / "web" / "app.js").read_text(encoding="utf-8")
        handler = source[source.index("$('#prepare-selected').addEventListener"):]

        upload_position = handler.index("await ensureOverlaysUploaded()")
        loop_position = handler.index("for (let index = 0; index < records.length; index += 1)")
        self.assertLess(upload_position, loop_position)

    def test_batch_settings_are_snapshotted_before_loop(self) -> None:
        source = (BASE_DIR / "web" / "app.js").read_text(encoding="utf-8")
        handler = source[source.index("$('#prepare-selected').addEventListener"):]

        settings_position = handler.index("const batchSettings = currentDownloadSettings()")
        loop_position = handler.index("for (let index = 0; index < records.length; index += 1)")
        self.assertLess(settings_position, loop_position)
        self.assertIn("logoTokens, batchSettings", handler[loop_position:])


if __name__ == "__main__":
    unittest.main()
