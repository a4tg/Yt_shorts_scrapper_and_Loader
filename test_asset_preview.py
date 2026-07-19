import zipfile
from pathlib import Path
from types import SimpleNamespace

import asset_preview
from asset_preview import (
    build_preview_data,
    ensure_video_proxy,
    needs_video_proxy,
    preview_capabilities,
    video_proxy_path,
)


def test_preview_capabilities_cover_media_documents_and_unknown() -> None:
    assert preview_capabilities("frame.webp")["kind"] == "image"
    assert preview_capabilities("clip.mp4")["kind"] == "video"
    assert preview_capabilities("brief.pdf")["kind"] == "pdf"
    assert preview_capabilities("report.xlsx")["kind"] == "table"
    assert preview_capabilities("payload.exe")["can_preview"] is False


def test_nonportable_video_formats_require_browser_proxy() -> None:
    assert needs_video_proxy("campaign.MOV") is True
    assert needs_video_proxy("cut.mkv") is True
    assert needs_video_proxy("ready.mp4") is False


def test_video_proxy_is_h264_faststart_and_reused(tmp_path, monkeypatch) -> None:
    source = tmp_path / "camera.mov"
    source.write_bytes(b"original")
    calls: list[list[str]] = []
    monkeypatch.setattr(asset_preview.shutil, "which", lambda name: "/usr/bin/ffmpeg")

    def fake_run(command, **kwargs):
        calls.append(command)
        Path(command[-1]).write_bytes(b"browser-safe")
        return SimpleNamespace(returncode=0, stderr="")

    monkeypatch.setattr(asset_preview.subprocess, "run", fake_run)

    first = ensure_video_proxy(source, "camera.mov")
    second = ensure_video_proxy(source, "camera.mov")

    assert first == video_proxy_path(source)
    assert second == first
    assert first.read_bytes() == b"browser-safe"
    assert len(calls) == 1
    assert "libx264" in calls[0]
    assert "+faststart" in calls[0]


def test_xlsx_preview_extracts_shared_string_table(tmp_path) -> None:
    path = tmp_path / "report.xlsx"
    shared = b'''<?xml version="1.0" encoding="UTF-8"?>
    <sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><si><t>Name</t></si><si><t>Value</t></si><si><t>AAP</t></si></sst>'''
    sheet = b'''<?xml version="1.0" encoding="UTF-8"?>
    <worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>
      <row><c t="s"><v>0</v></c><c t="s"><v>1</v></c></row>
      <row><c t="s"><v>2</v></c><c><v>42</v></c></row>
    </sheetData></worksheet>'''
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("xl/sharedStrings.xml", shared)
        archive.writestr("xl/worksheets/sheet1.xml", sheet)
    preview = build_preview_data(path, "report.xlsx")
    assert preview["columns"] == ["Name", "Value"]
    assert preview["rows"] == [["AAP", "42"]]


def test_xlsx_preview_preserves_sparse_column_coordinates(tmp_path) -> None:
    path = tmp_path / "sparse.xlsx"
    sheet = b'''<?xml version="1.0" encoding="UTF-8"?>
    <worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>
      <row><c r="A1" t="inlineStr"><is><t>Name</t></is></c><c r="C1" t="inlineStr"><is><t>Total</t></is></c></row>
      <row><c r="A2" t="inlineStr"><is><t>AAP</t></is></c><c r="C2"><v>42</v></c></row>
    </sheetData></worksheet>'''
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("xl/worksheets/sheet1.xml", sheet)

    preview = build_preview_data(path, "sparse.xlsx")

    assert preview["columns"] == ["Name", "Столбец 2", "Total"]
    assert preview["rows"] == [["AAP", "", "42"]]
