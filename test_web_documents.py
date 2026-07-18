from pathlib import Path


ROOT = Path(__file__).resolve().parent


def test_documents_have_a_dedicated_collaborative_editor() -> None:
    html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
    script = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
    styles = (ROOT / "web" / "documents.css").read_text(encoding="utf-8")

    for element_id in (
        "document-editor-dialog",
        "document-editor-title",
        "document-save-status",
        "document-format-toolbar",
        "document-editor-body-input",
        "document-reader",
        "document-comment-form",
        "document-comments-list",
        "document-revisions-list",
    ):
        assert f'id="{element_id}"' in html
    assert "/assets/documents.css" in html
    assert "openDocumentEditor(item.id" in script
    assert "createDocumentAndOpen" in script
    assert "scheduleDocumentAutosave" in script
    assert "expected_revision: item.revision_version" in script
    assert "ctrlKey || event.metaKey" in script
    assert ".document-editor-dialog" in styles


def test_document_versions_comments_and_reading_mode_are_wired() -> None:
    script = (ROOT / "web" / "app.js").read_text(encoding="utf-8")

    assert "/comments`)" in script
    assert "/document-comments/${comment.id}" in script
    assert "/revisions/${revision.id}/restore" in script
    assert "quoted_text: editor.value.slice" in script
    assert "start_offset: editor.selectionStart" in script
    assert "renderDocumentReader()" in script
    assert "appendDocumentInline" in script
