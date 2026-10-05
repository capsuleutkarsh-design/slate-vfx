"""
Every PDF Slate writes - the bid for the client (QPdfWriter), a workstation's
specification (QPrinter) and the transfer report (reportlab) - produced
offscreen and opened again. No PDF reader is installed, so the file is read
here: header and trailer, the page tree, and the text decoded through each
font's ToUnicode map (Qt) or its literal strings (reportlab).
"""

import base64
import re
import zlib
from decimal import Decimal
from pathlib import Path

import pytest

pytestmark = pytest.mark.realtools

_OBJ = re.compile(rb"(\d+) 0 obj(.*?)endobj", re.S)
_STREAM = re.compile(rb"stream\r?\n(.*?)(?:\r?\n)?endstream", re.S)


def _objects(data: bytes):
    """{number: (dictionary bytes, decoded stream or None)}"""
    out = {}
    for m in _OBJ.finditer(data):
        body = m.group(2)
        s = _STREAM.search(body)
        head = body[:s.start()] if s else body
        stream = None
        if s:
            stream = s.group(1)
            if b"ASCII85Decode" in head:
                stream = base64.a85decode(stream.strip().rstrip(b"~>").strip(), adobe=False)
            if b"FlateDecode" in head:
                stream = zlib.decompress(stream)
        out[int(m.group(1))] = (head, stream)
    return out


def _cmap(stream: bytes):
    codes = {}
    for block in re.findall(rb"beginbfchar(.*?)endbfchar", stream, re.S):
        for src, dst in re.findall(rb"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>", block):
            codes[int(src, 16)] = bytes.fromhex(dst.decode()).decode("utf-16-be")
    for block in re.findall(rb"beginbfrange(.*?)endbfrange", stream, re.S):
        for lo, hi, dst in re.findall(rb"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>\s*(\[[^\]]*\]|<[0-9A-Fa-f]+>)",
                                      block):
            lo, hi = int(lo, 16), int(hi, 16)
            if dst.startswith(b"["):
                for i, item in enumerate(re.findall(rb"<([0-9A-Fa-f]+)>", dst)):
                    codes[lo + i] = bytes.fromhex(item.decode()).decode("utf-16-be")
            else:
                start = int(dst[1:-1], 16)
                for i in range(hi - lo + 1):
                    codes[lo + i] = chr(start + i)
    return codes


def _literal(raw: bytes) -> str:
    out, i = bytearray(), 0
    while i < len(raw):
        c = raw[i]
        if c == 0x5C:  # backslash
            nxt = raw[i + 1:i + 2]
            octal = re.match(rb"[0-7]{1,3}", raw[i + 1:i + 4])
            if octal:
                out.append(int(octal.group(), 8) & 0xFF)
                i += 1 + len(octal.group())
                continue
            out += {b"n": b"\n", b"r": b"\r", b"t": b"\t"}.get(nxt, nxt)
            i += 2
            continue
        out.append(c)
        i += 1
    return out.decode("cp1252", errors="replace")


def read_pdf(path: Path):
    """(page count, the text on the pages) of a real PDF file."""
    data = Path(path).read_bytes()
    assert data.startswith(b"%PDF-1."), data[:16]
    assert data.rstrip().endswith(b"%%EOF")
    assert b"startxref" in data[-64:]
    objs = _objects(data)
    pages = [n for n, (head, _s) in objs.items() if re.search(rb"/Type\s*/Page(?![a-z])", head)]
    counts = [int(re.search(rb"/Count\s+(\d+)", head).group(1)) for head, _s in objs.values()
              if re.search(rb"/Type\s*/Pages(?![a-z])", head)]
    assert counts and max(counts) == len(pages), (counts, len(pages))

    maps = {}
    for n, (head, _s) in objs.items():
        ref = re.search(rb"/ToUnicode\s+(\d+)\s+0\s+R", head)
        if ref:
            maps[n] = _cmap(objs[int(ref.group(1))][1])

    text = []
    for n in pages:
        head = objs[n][0]
        res = re.search(rb"/Resources\s+(\d+)\s+0\s+R", head)
        res_body = objs[int(res.group(1))][0] if res else head
        fonts = {name: int(num) for name, num in
                 re.findall(rb"/(\w+)\s+(\d+)\s+0\s+R", res_body.split(b"/Font", 1)[-1].split(b">>", 1)[0])}
        contents = re.search(rb"/Contents\s+(?:\[([^\]]*)\]|(\d+)\s+0\s+R)", head)
        refs = re.findall(rb"(\d+)\s+0\s+R", contents.group(1)) if contents.group(1) else [contents.group(2)]
        for ref in refs:
            stream = objs[int(ref)][1]
            font = None
            for tok in re.finditer(rb"/(\w+)\s+[\d.]+\s+Tf|<([0-9A-Fa-f]*)>\s*Tj|\(((?:\\.|[^\\)])*)\)\s*Tj",
                                   stream, re.S):
                if tok.group(1):
                    font = maps.get(fonts.get(tok.group(1)))
                elif tok.group(2) is not None and font is not None:
                    hexs = tok.group(2)
                    text.append("".join(font.get(int(hexs[i:i + 4], 16), "?")
                                        for i in range(0, len(hexs), 4)))
                elif tok.group(3) is not None:
                    text.append(_literal(tok.group(3)))
    return len(pages), "".join(text)


def flat(text: str) -> str:
    return re.sub(r"\s+", "", text)


@pytest.fixture
def qapp():
    QtWidgets = pytest.importorskip("PySide6.QtWidgets")
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture
def offline_settings(monkeypatch):
    """The studio name without a database (get_setting reads the server)."""
    from slate.core.infra import studio_settings
    monkeypatch.setattr(studio_settings, "get_setting",
                        lambda key, default=None, db=None: "UT Studios ✦" if key == "studio_name"
                        else studio_settings.DEFAULTS.get(key, default))


def test_bid_pdf_for_the_client(tmp_path, qapp, offline_settings):
    """Bidding > Export bid as PDF: the figures the client reads, cost and margin left out."""
    from PySide6.QtWidgets import QWidget
    from slate.core.domain import bidding as DB
    from slate.core.infra.bid_repository import Bid
    from slate.gui.tabs.prod_bidding_tab import export_bid_pdf

    DB.set_overrides({})
    bid = Bid(project_code="AVTR3", project_name="Avatar 3", client_name="Zoë & Co <Films>",
              currency="INR", revision=2, notes="Two rounds of notes.\nThen final.")
    lines = [DB.BidLine(label=f"SH{n:03d} comp", department="comp", shot_count=1,
                        days_per_shot=Decimal("0.5"), day_rate=Decimal(8000), shot_name=f"SH{n:03d}")
             for n in range(10, 10 + 10 * 90, 10)]
    totals = DB.price_bid(lines, 20, tax=18)
    path = tmp_path / "Bid AVTR3 é.pdf"
    parent = QWidget()
    try:
        assert export_bid_pdf(parent, bid, lines, totals, path=str(path)) == str(path)
    finally:
        parent.deleteLater()

    pages, text = read_pdf(path)
    assert pages >= 2, "90 lines on one A4 page"
    text = flat(text)
    assert "UTStudios✦" in text and "BidAVTR3v2" in text
    assert "Zoë&Co<Films>" in text, "client name lost or shown as HTML"
    assert "SH010comp" in text and "SH900comp" in text, "first or last line missing"
    assert "GST(18%)" in text
    assert "Thenfinal." in text
    assert "Margin" not in text and "Dayrate" not in text, "internal figures reached the client"


def test_bid_pdf_total_in_rupee_grouping(tmp_path, qapp, offline_settings):
    from PySide6.QtWidgets import QWidget
    from slate.core.domain import bidding as DB
    from slate.core.infra.bid_repository import Bid
    from slate.gui.tabs.prod_bidding_tab import export_bid_pdf

    DB.set_overrides({})
    bid = Bid(project_code="AVTR3", client_name="Zoë", currency="INR", revision=1)
    lines = [DB.BidLine(label="SH010 comp", department="comp", shot_count=1,
                        days_per_shot=Decimal(3), day_rate=Decimal(80000), shot_name="SH010")]
    totals = DB.price_bid(lines, 20, tax=18)
    path = tmp_path / "bid.pdf"
    parent = QWidget()
    try:
        assert export_bid_pdf(parent, bid, lines, totals, path=str(path))
    finally:
        parent.deleteLater()
    pages, text = read_pdf(path)
    assert pages == 1
    assert "₹3,54,000.00" in flat(text)


def test_workstation_spec_pdf(tmp_path, qapp, monkeypatch):
    """Admin > a PC's specs > Export PDF, through the dialog's own button handler."""
    from PySide6.QtWidgets import QFileDialog, QMessageBox
    from slate.gui.admin_widgets import PCDetailsDialog

    path = tmp_path / "ART 01 é.pdf"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (str(path), "PDF (*.pdf)"))
    said = []
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: said.append(a[2]))
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: said.append("WARN " + a[2]))
    data = {"pc_name": "ART-01", "user": "राम", "Model": "Box <Pro> & Co",
            "CPU": "Intel i9", "RAM_GB": "64 GB", "SerialNo": "SN\x0012",
            "Drives": [{"Root": "C:\\", "Label": "Système", "Capacity_GB": 953.8,
                        "Free_GB": 50.1, "Usage": "94.7%"}]}
    dialog = PCDetailsDialog(data, hub=object(), pc_name="ART-01")
    try:
        assert dialog.export_to_pdf() is True, said
    finally:
        dialog.deleteLater()
    assert said and said[0].startswith("Saved"), said
    pages, text = read_pdf(path)
    assert pages >= 1
    text = flat(text)
    for want in ("Systemspecification", "ART-01", "राम", "Box<Pro>&Co", "Système", "64GB", "SN12"):
        assert flat(want) in text, (want, text)


class _FakeDB:
    """The two rows the report asks the database for (the PDF is what is checked)."""

    def __init__(self, project, tasks):
        self.project, self.tasks = project, tasks

    def execute_query(self, sql, params=None, fetch=None):
        if "FROM projects" in sql:
            return self.project
        if "COUNT(*)" in sql:
            return {"count": len(self.tasks)}
        return self.tasks


def test_transfer_report_pdf(tmp_path):
    """Settings > Project summary report (reportlab), with names that are not plain ASCII."""
    pytest.importorskip("reportlab")
    pytest.importorskip("pandas")
    from slate.utils.reporting import ReportGenerator

    tasks = [{"item_name": f"plate_{i}.exr", "dest_path": f"D:/Shows/R&D/SH010/plate_{i}.exr",
              "file_size": 1024 * 1024 * (i + 1), "duration": 2.0, "status": "Success"}
             for i in range(3)]
    tasks.append({"item_name": "plate copy (1).exr", "dest_path": "D:/Shows/A & B/x/plate copy (1).exr",
                  "file_size": 10, "duration": 0, "status": "Failed"})
    gen = ReportGenerator()
    gen.db_manager = _FakeDB({"id": 1, "name": "Show & Tell <2026>", "created_at": "2026-10-05"}, tasks)
    out = tmp_path / "report é.pdf"
    ok, message = gen.generate_project_summary_report(out, project_id=1)
    assert ok, message
    pages, text = read_pdf(out)
    assert pages >= 1
    text = flat(text)
    assert "Show&Tell<2026>" in text
    assert "D:\\Shows\\R&D\\SH010" in text, "a folder with '&' printed mangled"
    assert "SuspiciousFilesDetected" in text
    assert "platecopy(1).exr" in text and "D:/Shows/A&B/x/platecopy(1).exr" in text
