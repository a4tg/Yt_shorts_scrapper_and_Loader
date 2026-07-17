from pathlib import Path


ROOT = Path(__file__).resolve().parent


def test_asset_review_ui_respects_decision_roles_and_supports_withdrawal() -> None:
    script = (ROOT / "web" / "modules" / "asset-reviews.js").read_text(encoding="utf-8")
    styles = (ROOT / "web" / "asset-reviews.css").read_text(encoding="utf-8")

    assert "function canDecide(context)" in script
    assert "'owner', 'admin', 'editor', 'client'" in script
    assert "data-decision-clear" in script
    assert "method: 'DELETE'" in script
    assert "parent.visibility" in script
    assert "[data-decision-clear]" in styles
