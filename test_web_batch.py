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

    def test_selected_videos_are_sent_in_one_server_batch(self) -> None:
        source = (BASE_DIR / "web" / "app.js").read_text(encoding="utf-8")
        handler = source[source.index("$('#prepare-selected').addEventListener"):]
        self.assertIn("submitVideoBatch(selectedRecords())", handler)
        self.assertIn("api('/api/videos/download/batch'", source)
        self.assertIn("items: records.map", source)
        self.assertNotIn("await startDownloadUrl(\n        record.item.url", handler)

    def test_overlays_are_uploaded_once_before_batch_loop(self) -> None:
        source = (BASE_DIR / "web" / "app.js").read_text(encoding="utf-8")
        function = source[source.index("async function submitVideoBatch(records)"):]
        self.assertIn("const logoTokens = await ensureOverlaysUploaded()", function)
        self.assertIn("downloadPayload(", function)

    def test_batch_settings_are_snapshotted_before_loop(self) -> None:
        source = (BASE_DIR / "web" / "app.js").read_text(encoding="utf-8")
        function = source[source.index("async function submitVideoBatch(records)"):]
        self.assertIn("const batchSettings = currentDownloadSettings()", function)
        self.assertIn("record.item.url, logoTokens, batchSettings", function)

    def test_batch_cards_restore_status_and_retry_only_failures(self) -> None:
        html = (BASE_DIR / "web" / "index.html").read_text(encoding="utf-8")
        source = (BASE_DIR / "web" / "app.js").read_text(encoding="utf-8")
        self.assertIn('id="retry-failed"', html)
        self.assertIn("function setRecordJobState(record, job)", source)
        self.assertIn("function restoreDownloadJobs()", source)
        self.assertIn("queue_position", source)
        self.assertIn("record.failed", source)
        self.assertIn("api('/api/jobs/statuses'", source)


if __name__ == "__main__":
    unittest.main()
