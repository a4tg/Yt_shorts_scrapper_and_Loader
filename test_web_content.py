import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent


class WebContentWorkspaceTests(unittest.TestCase):
    def test_content_plan_documents_library_and_editor_are_rendered(self) -> None:
        html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
        for element_id in (
            "content-board",
            "content-calendar",
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


if __name__ == "__main__":
    unittest.main()
