import zipfile

from asset_preview import build_preview_data, preview_capabilities


def test_preview_capabilities_cover_media_documents_and_unknown() -> None:
    assert preview_capabilities("frame.webp")["kind"] == "image"
    assert preview_capabilities("clip.mp4")["kind"] == "video"
    assert preview_capabilities("brief.pdf")["kind"] == "pdf"
    assert preview_capabilities("report.xlsx")["kind"] == "table"
    assert preview_capabilities("payload.exe")["can_preview"] is False


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
