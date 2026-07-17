from pathlib import Path


ROOT = Path(__file__).resolve().parent


def test_asset_viewer_handles_media_failures_and_readiness() -> None:
    script = (ROOT / "web" / "modules" / "asset-viewer.js").read_text(encoding="utf-8")

    assert "watchMedia(video" in script
    assert "watchMedia(audio" in script
    assert "Браузер не смог воспроизвести это видео" in script
    assert "asset:media-ready" in script
    assert "setImageControls(kind === 'image')" in script


def test_asset_viewer_dialog_keeps_escape_and_keyboard_focus_accessible() -> None:
    script = (ROOT / "web" / "modules" / "asset-viewer.js").read_text(encoding="utf-8")

    assert "if (event.key === 'Escape')" in script
    assert "if (event.key === 'Tab')" in script
    assert "button:not([disabled])" in script
    assert "event.target.isContentEditable" in script
