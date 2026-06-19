from __future__ import annotations

import sys
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET


NS = {
    "main": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
    "rel": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "pkg": "http://schemas.openxmlformats.org/package/2006/relationships",
}


def main() -> int:
    workbook_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(
        "agent_tracker/market_lens_agent_portfolio_budget_100k.xlsx"
    )
    if not workbook_path.exists():
        print("false")
        return 0

    try:
        has_positions = workbook_has_open_positions(workbook_path)
    except Exception:
        # Conservative fallback: let the monitor run when the cheap precheck cannot read the workbook.
        print("true")
        return 0

    print("true" if has_positions else "false")
    return 0


def workbook_has_open_positions(path: Path) -> bool:
    with zipfile.ZipFile(path) as archive:
        workbook_xml = ET.fromstring(archive.read("xl/workbook.xml"))
        rels_xml = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
        rel_targets = {
            rel.attrib["Id"]: rel.attrib["Target"]
            for rel in rels_xml.findall("pkg:Relationship", NS)
        }

        sheet_target = ""
        for sheet in workbook_xml.findall("main:sheets/main:sheet", NS):
            if sheet.attrib.get("name") == "Open Positions":
                relationship_id = sheet.attrib.get(f"{{{NS['rel']}}}id", "")
                sheet_target = rel_targets.get(relationship_id, "")
                break
        if not sheet_target:
            return False

        normalized_target = sheet_target.lstrip("/")
        sheet_path = normalized_target if normalized_target.startswith("xl/") else f"xl/{normalized_target}"
        sheet_xml = ET.fromstring(archive.read(sheet_path))
        shared_strings = read_shared_strings(archive)
        rows = sheet_xml.findall("main:sheetData/main:row", NS)
        for row in rows:
            if int(row.attrib.get("r", "0")) <= 1:
                continue
            ticker = first_cell_text(row, shared_strings)
            if ticker:
                return True
        return False


def read_shared_strings(archive: zipfile.ZipFile) -> list[str]:
    try:
        xml = ET.fromstring(archive.read("xl/sharedStrings.xml"))
    except KeyError:
        return []
    values = []
    for item in xml.findall("main:si", NS):
        values.append("".join(text.text or "" for text in item.findall(".//main:t", NS)).strip())
    return values


def first_cell_text(row: ET.Element, shared_strings: list[str]) -> str:
    cells = row.findall("main:c", NS)
    if not cells:
        return ""
    first = cells[0]
    value = first.find("main:v", NS)
    if value is None or value.text is None:
        return ""
    raw = value.text.strip()
    if first.attrib.get("t") == "s":
        try:
            return shared_strings[int(raw)].strip()
        except (IndexError, ValueError):
            return ""
    return raw


if __name__ == "__main__":
    raise SystemExit(main())
