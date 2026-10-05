"""
Every table that leaves Slate as CSV or XLSX - table_export (IT tables, the
bid list, bid tracking), and the fleet report - written to disk and read back
with the real csv module and openpyxl. A cell that looks like a formula must
reach Excel as text; numbers and dates must stay numbers and dates.
"""

import csv
import json
import os
import time
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import pytest

pytestmark = pytest.mark.realtools
openpyxl = pytest.importorskip("openpyxl")

from slate.core.domain.table_export import export_rows  # noqa: E402

TRICKY = [
    "plain",
    "Zoë – राहुल ₹ 🎬",
    'comma, "quote" and\nnewline',
    "=1+1",
    "+cmd|' /C calc'!A0",
    "-5",
    "@SUM(A1)",
    "\tTab first",
    "＝fullwidth",
    "'=already quoted",
    "",
    "bell\x07 and vt\x0b from a pasted mail",
]
HEADERS = ["Name", "Count", "Money", "When", "Day", "Empty"]


def rows():
    out = []
    for i, text in enumerate(TRICKY):
        out.append([text, -i, Decimal("1234.50"), datetime(2026, 10, 5, 9, 30),
                    date(2026, 10, i + 1), None])
    return out


def formulas(path):
    book = openpyxl.load_workbook(path)
    return [(ws.title, c.coordinate, c.value) for ws in book.worksheets
            for row in ws.iter_rows() for c in row if c.data_type == "f"]


def expected_text(text):
    """What a spreadsheet must show: the text, with an apostrophe if it would run as a formula."""
    risky = text.lstrip("'").startswith(("=", "+", "-", "@", "\t", "\r", "\n", "＝"))
    return "'" + text if risky else text


def test_csv_reads_back_exactly_and_safely(tmp_path):
    path = tmp_path / "IT licences é.csv"
    assert export_rows(path, HEADERS, rows()) == len(TRICKY)
    raw = path.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf"), "no BOM: Excel opens UTF-8 as mojibake"
    with open(path, encoding="utf-8-sig", newline="") as fh:
        table = list(csv.reader(fh))
    assert table[0] == HEADERS
    assert len(table) == 1 + len(TRICKY)
    for i, (text, row) in enumerate(zip(TRICKY, table[1:])):
        assert row[0] == expected_text(text), f"row {i}"
        assert row[1] == str(-i)                 # a negative number is not quoted
        assert row[2] == "1234.50"
        assert row[3] == "2026-10-05 09:30:00"
        assert row[4] == f"2026-10-{i + 1:02d}"
        assert row[5] == ""


def test_xlsx_keeps_types_and_never_holds_a_formula(tmp_path):
    path = tmp_path / "IT licences.xlsx"
    assert export_rows(path, HEADERS, rows()) == len(TRICKY)
    assert formulas(path) == []
    ws = openpyxl.load_workbook(path).active
    assert [c.value for c in ws[1]] == HEADERS
    assert all(c.font.b for c in ws[1])
    assert ws.freeze_panes == "A2"
    for i, text in enumerate(TRICKY):
        name, count, money, when, day, empty = (c.value for c in ws[i + 2])
        want = expected_text(text)
        for bad in "\x07\x0b":
            want = want.replace(bad, "")       # characters an .xlsx cannot hold
        assert (name or "") == want, f"row {i}"
        assert count == -i and isinstance(count, int)
        assert money == 1234.5
        assert when == datetime(2026, 10, 5, 9, 30)
        assert isinstance(day, datetime) and day.date() == date(2026, 10, i + 1)
        assert empty is None


def _bids():
    from slate.core.infra.bid_repository import Bid
    common = dict(currency="INR", status="approved", created_by="priya",
                  created_at="2026-09-30T10:00:00")
    return [
        Bid(project_code="AVTR3", project_name="Avatar 3", client_name="=cmd|bad", revision=2,
            shot_count=10, estimated_days=Decimal("30.5"), estimated_cost=Decimal("240000"),
            estimated_budget=Decimal("300000"), tax_amount=Decimal("54000"),
            total_amount=Decimal("354000"), **common),
        Bid(project_code="DUNE", project_name="Dune, Part \"3\"", client_name="Zoë Films",
            revision=1, shot_count=3, estimated_days=Decimal("4"), estimated_cost=Decimal("0.10"),
            estimated_budget=Decimal("0.20"), tax_amount=Decimal("0.04"),
            total_amount=Decimal("0.24"), **common),
    ]


@pytest.mark.parametrize("suffix", [".xlsx", ".csv"])
def test_bid_list_export_adds_up(tmp_path, suffix):
    """The Bidding tab's Export list: amounts as plain numbers a spreadsheet can total."""
    from slate.core.domain.bid_export import LIST_HEADERS, bid_list_rows
    headers, table = bid_list_rows(_bids(), names={"priya": "Priya S"})
    path = tmp_path / f"bids{suffix}"
    assert export_rows(path, headers, table) == 2
    if suffix == ".xlsx":
        assert formulas(path) == []
        ws = openpyxl.load_workbook(path).active
        got = [[c.value for c in row] for row in ws.iter_rows()]
    else:
        with open(path, encoding="utf-8-sig", newline="") as fh:
            got = list(csv.reader(fh))
    assert got[0] == LIST_HEADERS
    col = {h: i for i, h in enumerate(LIST_HEADERS)}
    first, second = got[1], got[2]
    assert first[col["Client"]] == "'=cmd|bad"
    assert second[col["Project name"]] == 'Dune, Part "3"'
    assert first[col["Status"]] == "Won" and first[col["Created by"]] == "Priya S"
    if suffix == ".xlsx":
        assert first[col["Total"]] + second[col["Total"]] == pytest.approx(354000.24)
        assert first[col["Artist days"]] == 30.5 and first[col["Shots"]] == 10
        assert first[col["Created"]].date() == date(2026, 9, 30)
    else:
        assert Decimal(first[col["Total"]]) + Decimal(second[col["Total"]]) == Decimal("354000.24")
        assert first[col["Created"]] == "2026-09-30"


def _fleet_reports(folder: Path, now: float):
    folder.mkdir()
    reports = {
        "ART-01": {"pc_name": "ART-01", "user": "=HYPERLINK(\"x\")", "IPAddress": "10.0.0.5",
                   "RAM_GB": "32 GB", "CPU": "Intel, \"i9\"", "GPU": "RTX – 4090",
                   "Drives": [{"Root": "C:\\", "Label": "Système", "Capacity_GB": 953.8,
                               "Free_GB": 50.1, "Usage": "94.7%"},
                              {"Root": None, "Label": "-weird", "Usage": "10%"}]},
        "ART-02": {"pc_name": "ART-02", "user": "ana", "RAM_GB": 64, "Drives": [],
                   # BIOS strings arrive with control characters in them.
                   "SerialNo": "SN\u000012\u000b34"},
    }
    for name, data in reports.items():
        (folder / f"{name}.json").write_text(json.dumps(data), encoding="utf-8")
    (folder / "BROKEN.json").write_text("{not json", encoding="utf-8")
    os.utime(folder / "ART-01.json", (now - 30, now - 30))
    os.utime(folder / "ART-02.json", (now - 86400 * 3, now - 86400 * 3))
    return sorted(folder.glob("*.json"))


@pytest.mark.parametrize("suffix", [".xlsx", ".csv", ".json"])
def test_fleet_report_files(tmp_path, suffix):
    """Admin > Fleet report from real LiveStatus files, opened with the real readers."""
    pytest.importorskip("PySide6")
    from slate.gui.admin_fleet_report_service import write_report
    now = time.time()
    files = _fleet_reports(tmp_path / "LiveStatus", now)
    out = tmp_path / f"fleet report{suffix}"
    result = write_report(out, files, now=now)
    assert result["ok"], result
    assert result["records"] == 2 and result["skipped"] == 1

    if suffix == ".json":
        data = json.loads(out.read_text(encoding="utf-8"))
        assert data["summary"]["total"] == 2 and data["summary"]["skipped_files"] == 1
        art01 = next(w for w in data["workstations"] if w["pc_name"] == "ART-01")
        assert art01["ram_gb"] == 32 and art01["drive_c_usage_pct"] == 94.7
        return

    if suffix == ".xlsx":
        assert formulas(out) == []
        book = openpyxl.load_workbook(out)
        tiles = [c.value for c in book["Summary"][4]][:6]
        assert tiles == [2, 1, 0, 1, 0, 1]
        ws = book["Fleet Data"]
        table = [[c.value for c in row] for row in ws.iter_rows()]
    else:
        with open(out, encoding="utf-8-sig", newline="") as fh:
            table = list(csv.reader(fh))
        assert "1 report file could not be read" in out.with_name(
            out.stem + "_summary.txt").read_text(encoding="utf-8")
    header = table[0]
    assert header[:3] == ["Machine", "Status", "Last report"]
    rows_by = {r[0]: dict(zip(header, r)) for r in table[1:]}
    art01 = rows_by["ART-01"]
    assert art01["Status"] == "Online" and rows_by["ART-02"]["Status"] == "Offline"
    assert art01["Slate user"] == "'=HYPERLINK(\"x\")"
    assert art01["CPU"] == 'Intel, "i9"'
    assert art01["C: label"] == "Système"
    assert str(art01["RAM (GB)"]) == "32"
    assert str(art01["C: used %"]) == "94.7"
    assert art01["C: alert"] == "CRITICAL"
    assert "Drive without a letter 1: label" in header
    assert art01["Drive without a letter 1: label"] == "'-weird"
    serial = rows_by["ART-02"]["Serial number"]
    assert serial == "SN1234"
