import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent


class WebContentWorkspaceTests(unittest.TestCase):
    def test_content_plan_documents_library_and_editor_are_rendered(self) -> None:
        html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
        for element_id in (
            "content-board",
            "content-calendar",
            "content-calendar-toolbar",
            "calendar-month-label",
            "calendar-previous",
            "calendar-today",
            "calendar-next",
            "content-search",
            "create-content-button",
            "documents-list",
            "create-document-button",
            "library-grid",
            "content-dialog",
            "content-body-input",
            "content-file-input",
            "content-revisions",
        ):
            self.assertIn(f'id="{element_id}"', html)

    def test_content_client_calls_project_scoped_apis_and_supports_dragging(self) -> None:
        script = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
        self.assertIn("/content`", script)
        self.assertIn("/library`", script)
        self.assertIn("/attachments`", script)
        self.assertIn("text/content-id", script)
        self.assertIn("moveContentToStage", script)
        self.assertIn("renderContentCalendar", script)
        self.assertIn("moveContentCalendar", script)
        self.assertIn("calendar-weekday", script)
        self.assertIn("renderRevisions", script)

    def test_content_workspace_has_responsive_layout_rules(self) -> None:
        css = (ROOT / "web" / "workspace.css").read_text(encoding="utf-8")
        for selector in (
            ".content-board",
            ".content-calendar",
            ".content-dialog",
            ".documents-grid",
            ".library-grid",
        ):
            self.assertIn(selector, css)

    def test_asset_viewer_supports_project_files_chat_attachments_and_document_kinds(self) -> None:
        html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
        entrypoint = (ROOT / "web" / "workspace-depth.js").read_text(encoding="utf-8")
        viewer = (ROOT / "web" / "modules" / "asset-viewer.js").read_text(encoding="utf-8")
        styles = (ROOT / "web" / "asset-viewer.css").read_text(encoding="utf-8")
        app = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
        self.assertIn('/assets/asset-viewer.css', html)
        self.assertIn("registerModule('asset-viewer'", entrypoint)
        for marker in ("asset:open", "preview_data_url", "asset-viewer-pdf", "renderTable", "requestFullscreen"):
            self.assertIn(marker, viewer)
        self.assertIn("data-asset-id", viewer)
        self.assertIn("dataset.assetId", app)
        self.assertIn("@media(max-width:900px)", styles)

    def test_asset_review_workspace_supports_versions_annotations_compare_and_approval(self) -> None:
        html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
        entrypoint = (ROOT / "web" / "workspace-depth.js").read_text(encoding="utf-8")
        reviews = (ROOT / "web" / "modules" / "asset-reviews.js").read_text(encoding="utf-8")
        styles = (ROOT / "web" / "asset-reviews.css").read_text(encoding="utf-8")
        self.assertIn('/assets/asset-reviews.css', html)
        self.assertIn("registerModule('asset-reviews'", entrypoint)
        for marker in (
            "annotation_type", "asset-review-overlay", "time_seconds", "page_number",
            "uploadVersion", "asset-compare", "changes_requested", "EventSource",
        ):
            self.assertIn(marker, reviews)
        for selector in (".asset-review-panel", ".asset-review-marker", ".asset-compare-side", ".asset-approval-state"):
            self.assertIn(selector, styles)


if __name__ == "__main__":
    unittest.main()
