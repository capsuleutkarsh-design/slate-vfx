"""Fleet report export (SYS-032..037)."""

import csv
import json
import os
import time
from pathlib import Path

import pytest

from slate.gui import admin_fleet_report_service as svc


def _write(folder: Path, name, **data):
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{name}.json"
    path.write_text(json.dumps(dict(pc_name=name, **data)), encoding="utf-8")
    if "last_seen" in data:
        # The file's own time is what freshness is judged by (SYS2-006).
        os.utime(path, (data["last_seen"], data["last_seen"]))


@pytest.fixture
def reports(tmp_path):
    now = time.time()
    folder = tmp_path / "LiveStatus"
    _write(folder, "COMP-01", last_seen=now - 5, user="rahul",
           Drives=[{"Root": "C:\\", "Label": "System", "Usage": "91%", "Capacity_GB": 500, "Free_GB": 45}])
    _write(folder, "COMP-02", last_seen=now - 120, user="",
           Drives=[{"Root": None, "Label": None, "Usage": None}])
    _write(folder, "COMP-03", last_seen=now - 900)
    _write(folder, "COMP-04")                       # never said when: the file is fresh
    (folder / "broken.json").write_text("{not json", encoding="utf-8")
    return folder, now


def test_csv_starts_with_the_header_and_reads_back(reports, tmp_path):
    folder, now = reports
    out = tmp_path / "fleet.csv"
    result = svc.write_report(out, sorted(folder.glob("*.json")), now=now)
    assert result["ok"]
    first = out.read_text(encoding="utf-8-sig").splitlines()[0]
    assert first.startswith("Machine,Status")
    rows = list(csv.DictReader(out.open(encoding="utf-8-sig")))
    assert len(rows) == 4
    header = list(rows[0].keys())
    assert "Slate user" in header and "C: used %" in header
    assert not any(h.startswith("disk_c") or "UT" in h for h in header)
    assert (tmp_path / "fleet_summary.txt").exists()


def test_summary_adds_up_and_reads_as_a_sentence(reports):
    folder, now = reports
    records, summary, skipped = svc.build_records(sorted(folder.glob("*.json")), now)
    assert skipped == 1
    assert summary["online"] + summary["not_responding"] + summary["offline"] + summary["unknown"] \
        == len(records) == 4
    # Problems first (SYS2-005); a report without last_seen whose file is fresh is online.
    assert [r["status"] for r in records] == ["Offline", "Not responding", "Online", "Online"]
    text = svc.summary_sentence(records, summary, skipped)
    assert text.startswith("4 machines: 2 online, 1 not responding, 1 offline, 0 unknown.")


def test_null_drive_root_does_not_crash(reports):
    folder, now = reports
    record = svc.record_for(json.loads((folder / "COMP-02.json").read_text()), "COMP-02", now)
    assert record["drive_noletter1_root"] == "?"


def test_json_keeps_ut_user(reports, tmp_path):
    folder, now = reports
    out = tmp_path / "fleet.json"
    svc.write_report(out, sorted(folder.glob("*.json")), now=now)
    data = json.loads(out.read_text())
    assert "ut_user" in data["workstations"][0]
    assert data["summary"]["unknown"] == 0
    assert "last_seen_age" not in data["workstations"][0]


def test_xlsx_has_no_divider_rows(reports, tmp_path):
    openpyxl = pytest.importorskip("openpyxl")
    folder, now = reports
    out = tmp_path / "fleet.xlsx"
    result = svc.write_report(out, sorted(folder.glob("*.json")), now=now)
    assert "Coloured" in result["message"]
    ws = openpyxl.load_workbook(out)["Fleet Data"]
    header = [c.value for c in ws[1]]
    assert header[:2] == ["Machine", "Status"] and "Slate user" in header
    assert ws.cell(row=2, column=1).value == "COMP-03"
    # The filter covers the data, not the header only; usage is a number (SYS2-010).
    assert ws.auto_filter.ref == ws.dimensions
    used = header.index("C: used %") + 1
    cell = next(c for c in ws.iter_rows(min_row=2, min_col=used, max_col=used) if c[0].value != None)[0]
    assert isinstance(cell.value, (int, float)) and cell.number_format == '0.0"%"'
    assert "Age" not in header and "Age (s)" in header
    for row in ws.iter_rows(min_row=2, values_only=True):
        assert not str(row[0] or "").startswith("  ")


def test_export_runs_on_a_worker(reports, qtbot, monkeypatch, tmp_path):
    from PySide6.QtWidgets import QFileDialog, QMessageBox, QWidget
    folder, _now = reports
    out = tmp_path / "fleet_ui.csv"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (str(out), "CSV Files (*.csv)"))
    shown = []
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: shown.append(a[2]))

    class Hub:
        def get_livestatus_dir(self):
            return folder
    parent = QWidget()
    qtbot.addWidget(parent)
    logged = []
    worker = svc.run_fleet_report_export(parent, Hub(), logged.append)
    assert isinstance(worker, svc.FleetReportWorker)
    qtbot.waitUntil(lambda: bool(shown), timeout=10000)
    assert "4 machines" in shown[0] and out.exists() and logged


def test_live_reporter_refreshes_disk_figures(monkeypatch, tmp_path):
    from slate.core.domain import live_reporter as lr

    class Hub:
        def get_livestatus_dir(self):
            return tmp_path

    calls = []
    monkeypatch.setattr(lr, "ServerHub", Hub)
    monkeypatch.setattr(lr.HardwareInfo, "get_static_specs", staticmethod(lambda: {}))
    monkeypatch.setattr(lr.HardwareInfo, "get_dynamic_specs",
                        staticmethod(lambda: calls.append(1) or {"Drives": [
                            {"Root": "C:", "Label": "System", "Usage": "45.0%"}]}))
    clock = [1_000_000.0]
    monkeypatch.setattr(lr.time, "time", lambda: clock[0])
    reporter = lr.LiveReporter("rahul")
    reporter.report_status()
    clock[0] += 60
    reporter.report_status()
    assert len(calls) == 1
    clock[0] += reporter.update_interval
    reporter.report_status()
    assert len(calls) == 2
    data = json.loads(next(tmp_path.glob("*.json")).read_text())
    assert data["drives_updated"] == clock[0] and data["disk_percent"] == 45.0


def test_volume_label_falls_back():
    from slate.core.system.hardware_info import HardwareInfo
    assert HardwareInfo.volume_label("?:") == "Local disk"


def test_a_drive_without_a_letter_does_not_overwrite_x():
    record = svc.record_for({"pc_name": "PC", "last_seen": 1, "Drives": [
        {"Root": "X:\\", "Label": "Archive", "Usage": "10%"},
        {"Root": None, "Label": "Mystery", "Usage": "20%"},
        {"Root": "", "Label": "Other", "Usage": "30%"}]}, "PC", 2)
    assert record["drive_x_label"] == "Archive"
    assert record["drive_noletter1_label"] == "Mystery" and record["drive_noletter2_label"] == "Other"
    assert svc.header_for("drive_noletter1_usage_pct") == "Drive without a letter 1: used %"


def test_unknown_words_and_ram_are_cleaned(tmp_path):
    """SYS2-040: 'Unknown' is not reported, RAM '32 GB' is the number 32."""
    record = svc.record_for({"pc_name": "A", "IPAddress": "Unknown", "RAM_GB": "32 GB",
                             "Manufacturer": "N/A", "CPU": "i9"}, "A", time.time())
    assert record["ip_address"] == "" and record["manufacturer"] == ""
    assert record["ram_gb"] == 32 and record["cpu"] == "i9"


def test_open_file_says_close_it_in_excel(reports, monkeypatch, tmp_path):
    """SYS2-025: a PermissionError is said in words."""
    folder, _now = reports

    def refuse(*_a, **_k):
        raise PermissionError(13, "Permission denied")
    monkeypatch.setattr(svc, "write_report", refuse)
    worker = svc.FleetReportWorker(tmp_path / "x.xlsx", [])
    got = []
    worker.done.connect(got.append)
    worker.run()
    assert not got[0]["ok"] and "close it" in got[0]["message"]
