from pathlib import Path


ROOT = Path(__file__).resolve().parent


def test_approval_workspace_is_a_real_queue_not_only_a_stage_builder() -> None:
    html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
    script = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
    styles = (ROOT / "web" / "approvals.css").read_text(encoding="utf-8")

    for element_id in (
        "approval-queue-summary",
        "approval-queue-filters",
        "approval-queue-list",
        "approval-request-open",
        "approval-request-dialog",
        "approval-request-form",
        "approval-request-attachment",
        "approval-request-assignee",
        "approval-request-stage",
        "approval-request-due",
        "approval-request-visibility",
    ):
        assert f'id="{element_id}"' in html
    assert '/assets/approvals.css' in html
    assert "/approval-queue?status=" in script
    assert "/approval-request`" in script
    assert "renderApprovalQueue()" in script
    assert "decideApproval(item, 'approved'" in script
    assert "decideApproval(item, 'changes_requested'" in script
    assert "/history`" in script
    assert ".approval-card" in styles
    assert "@media (max-width: 680px)" in styles


def test_approval_request_guards_client_visibility_and_editor_actions() -> None:
    script = (ROOT / "web" / "app.js").read_text(encoding="utf-8")

    assert "'owner', 'admin', 'editor', 'client'" in script
    assert "if (member?.role === 'client')" in script
    assert "$('#approval-request-visibility').value = 'client'" in script
    assert "approvalCanEdit() && item.status === 'pending'" in script
    assert "status: 'cancelled'" in script
