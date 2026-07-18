from pathlib import Path


ROOT = Path(__file__).resolve().parent


def test_decision_ui_enforces_roles_and_context_navigation() -> None:
    script = (ROOT / "web" / "modules" / "decision-intelligence.js").read_text(
        encoding="utf-8"
    )
    graph = (ROOT / "web" / "modules" / "project-graph.js").read_text(
        encoding="utf-8"
    )

    assert "function canContribute(context)" in script
    assert "briefing-ai" in script
    assert "status.disabled" in script
    assert "renderAssignees" in script
    assert "review:focus" in script
    assert "bridge.openDocument?.(queue.dataset.contentId)" in script
    assert "pending_approvals" in script
    assert "attention-queue-action" in script
    assert "router.open('graph', { insight: insightGraph })" in script
    assert "params.insight" in graph
