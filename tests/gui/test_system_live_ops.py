"""
Admin Panel: who sees what (HR-143, SYS-002), Live Ops cards and grid
(SYS-004/008/009/021-026/029-031/052/054-058), the API gateway (SYS-012).
"""

import json
import time
from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication, QLabel

from slate.core.domain import fleet_status as fs
from slate.core.infra.gate import Gate


class FakeHub:
    def __init__(self, root: Path):
        self.root = Path(root)
        (self.root / "LiveStatus").mkdir(parents=True, exist_ok=True)
        (self.root / "Attendance").mkdir(parents=True, exist_ok=True)
        self.posted = []

    def get_livestatus_dir(self):
        return self.root / "LiveStatus"

    def get_attendance_dir(self):
        return self.root / "Attendance"

    def post_command(self, command, target, payload=None, admin_user="", reason=""):
        self.posted.append((command, target, payload, admin_user, reason))


def _report(name, age=5, now=None, **extra):
    now = time.time() if now is None else now
    data = {"pc_name": name, "last_seen": now - age, "user": "rahul", "disk_percent": 40}
    data.update(extra)
    return data


@pytest.fixture
def hub(tmp_path):
    return FakeHub(tmp_path / "server")


@pytest.fixture
def dashboard(qtbot, hub):
    from slate.gui.admin_widgets import LiveDashboard
    board = LiveDashboard(hub)
    # No real read of the (empty) folder racing the data a test feeds in.
    board.refresh_grid = lambda *a: None
    qtbot.addWidget(board)
    board.resize(1600, 900)
    board.show()
    yield board
    board.cleanup()


# --------------------------------------------------------------- statuses
def test_status_words_and_thresholds():
    now = 1_000_000.0
    assert fs.status_for(now - 10, now) == fs.ONLINE
    assert fs.status_for(now - 120, now) == fs.NOT_RESPONDING
    assert fs.label(fs.status_for(now - 120, now)) == "Not responding"
    assert fs.status_for(now - 600, now) == fs.OFFLINE
    assert fs.status_for(None, now) == fs.UNKNOWN
    counts = fs.summarise([{"last_seen": now}, {"last_seen": now - 900}, {}], now)
    assert counts["total"] == 3 and counts[fs.UNKNOWN] == 1


def test_disk_values_are_coerced():
    assert fs.disk_percent("88%") == 88.0
    assert fs.disk_percent("abc") is None
    assert fs.disk_level(81) == "warn" and fs.disk_level(95.5) == "bad" and fs.disk_level(33) == "ok"


# ------------------------------------------------------------------ cards
def test_a_machine_that_goes_quiet_turns_offline(dashboard):
    now = time.time()
    dashboard.on_data_ready([_report("COMP-01", 5, now)], now=now)
    card = dashboard.pc_widgets["COMP-01"]
    assert "Online" in card.lbl_status.text()
    dashboard.on_data_ready([_report("COMP-01", 600, now)], now=now)
    card = dashboard.pc_widgets["COMP-01"]
    assert "Offline" in card.lbl_status.text() and "last seen" in card.lbl_status.text()
    assert Gate.BAD in card.lbl_status.styleSheet()


def test_offline_machines_stay_and_are_counted(dashboard):
    now = time.time()
    data = [_report(f"COMP-0{i}", 5, now) for i in range(3)]
    data += [_report("ROTO-01", 900, now), _report("ROTO-02", 1200, now)]
    dashboard.on_data_ready(data, now=now)
    assert len(dashboard.pc_widgets) == 5
    assert dashboard.summary_cards[fs.ONLINE].value_text() == "3"
    assert dashboard.summary_cards[fs.NOT_RESPONDING].value_text() == "0"
    assert dashboard.summary_cards[fs.OFFLINE].value_text() == "2"
    dashboard.search.setText("COMP-0")
    assert {c.pc_name for c in dashboard.visible_cards()} == {"COMP-00", "COMP-01", "COMP-02"}
    assert "Updated" in dashboard.lbl_updated.text()


def test_one_bad_report_does_not_stop_the_others(dashboard):
    now = time.time()
    dashboard.on_data_ready([_report("A", 5, now, disk_percent="88%"),
                             _report("B", 5, now, disk_percent="abc"),
                             _report("C", 5, now)], now=now)
    assert dashboard.pc_widgets["A"].lbl_disk.text() == "C: 88% full"
    assert "not reported" in dashboard.pc_widgets["B"].lbl_disk.text()
    assert dashboard.pc_widgets["C"].lbl_disk.text() == "C: 40% full"


def test_disk_text_is_rounded_and_coloured(dashboard):
    now = time.time()
    dashboard.on_data_ready([_report("A", 5, now, disk_percent=33.3333),
                             _report("B", 5, now, disk_percent=81),
                             _report("C", 5, now, disk_percent=95.5)], now=now)
    assert dashboard.pc_widgets["A"].lbl_disk.text() == "C: 33% full"
    assert Gate.WARN in dashboard.pc_widgets["B"].lbl_disk.styleSheet()
    assert Gate.BAD in dashboard.pc_widgets["C"].lbl_disk.styleSheet()


def test_nobody_signed_in_and_long_names_elide(dashboard):
    now = time.time()
    long_name = "COMP-WORKSTATION-FLOOR3-IMAGING-BAY-17"
    dashboard.on_data_ready([_report("R1", 5, now, user=""), _report("R2", 5, now, user="Unknown"),
                             _report(long_name, 5, now)], now=now)
    assert dashboard.pc_widgets["R1"].lbl_user.text() == "Nobody signed in"
    assert dashboard.pc_widgets["R2"].lbl_user.text() == "Nobody signed in"
    card = dashboard.pc_widgets[long_name]
    assert card.lbl_name.text().endswith("…") and long_name in card.lbl_name.toolTip()


def test_grid_has_no_holes_after_machines_leave(dashboard):
    now = time.time()
    names = [f"PC-{i}" for i in range(6)]
    dashboard.on_data_ready([_report(n, 5, now) for n in names], now=now)
    dashboard.on_data_ready([_report(n, 5, now) for n in names[:2] + names[4:]], now=now)
    layout = dashboard.grid_layout
    cols = dashboard.column_count()
    positions = sorted(layout.getItemPosition(layout.indexOf(c))[:2]
                       for c in dashboard.visible_cards())
    assert positions == [divmod(i, cols) for i in range(4)]
    dashboard.on_data_ready([], now=now)
    dashboard.on_data_ready([_report("Z", 5, now)], now=now)
    card = dashboard.pc_widgets["Z"]
    assert layout.getItemPosition(layout.indexOf(card))[:2] == (0, 0)


def test_columns_follow_the_width(dashboard, qtbot):
    dashboard.resize(1600, 900)
    QApplication.processEvents()
    narrow = dashboard.column_count()
    dashboard.resize(1920, 1080)
    QApplication.processEvents()
    assert dashboard.column_count() > narrow >= 5


def test_restart_asks_logs_and_is_hidden_read_only(monkeypatch, qtbot, hub):
    from slate.gui import admin_widgets
    asked = {}

    def fake_confirm(parent, title, text, yes_label="Continue", no_label="Cancel",
                     destructive=False, informative=""):
        asked.update(parent=parent, title=title, text=text, yes=yes_label, destructive=destructive)
        return True

    monkeypatch.setattr(admin_widgets, "confirm", fake_confirm)
    monkeypatch.setattr(admin_widgets, "ask_reason", lambda *a: "Driver update")
    logged = []
    card = admin_widgets.PCCard("COMP-01", hub, verify_callback=lambda: True, log_action=logged.append,
                                admin_user="boss")
    qtbot.addWidget(card)
    assert card.request_power("restart")
    assert asked["title"] == "Restart COMP-01?" and asked["destructive"] and asked["parent"] is not None
    assert "unsaved work" in asked["text"]
    assert hub.posted == [("restart", "COMP-01", None, "boss", "Driver update")]
    assert any("COMP-01" in line for line in logged)

    menu, _details, rst, off = card.build_menu()
    texts = [a.text() for a in menu.actions() if a.text()]
    assert texts == ["View system specs", "Restart…", "Shut down…"]

    viewer = admin_widgets.PCCard("COMP-02", hub, read_only=True)
    qtbot.addWidget(viewer)
    _menu, _d, rst, off = viewer.build_menu()
    assert rst is None and off is None and not viewer.request_power("shutdown")


def test_double_click_opens_specs(monkeypatch, qtbot, hub):
    from slate.gui import admin_widgets
    opened = []
    monkeypatch.setattr(admin_widgets.PCCard, "open_details", lambda self: opened.append(self.pc_name))
    card = admin_widgets.PCCard("COMP-01", hub)
    qtbot.addWidget(card)
    card.show()
    from PySide6.QtCore import Qt
    qtbot.mouseDClick(card, Qt.MouseButton.LeftButton)
    assert opened == ["COMP-01"]


# ---------------------------------------------------------------- details
def test_details_dialog_fits_storage_first_and_reloads_quietly(monkeypatch, qtbot, hub):
    from slate.gui import admin_widgets
    from PySide6.QtWidgets import QMessageBox
    boxes = []
    for name in ("information", "warning", "critical"):
        monkeypatch.setattr(QMessageBox, name, lambda *a, **k: boxes.append(a))
    data = _report("COMP-01", 5, Drives=[{"Root": "C:\\", "Label": "System", "Usage": "97%"},
                                          {"Root": "D:\\", "Label": "Work", "Usage": "56%"}],
                   WindowsVersion="23H2", SerialNo="SN1", os_user="rahul")
    dlg = admin_widgets.PCDetailsDialog(data, hub=hub, pc_name="COMP-01")
    qtbot.addWidget(dlg)
    screen = dlg.screen().availableGeometry()
    assert dlg.height() <= max(700 * 1, int(screen.height() * 0.85)) and dlg.height() <= 700
    assert dlg.section_titles[0] == "Storage"
    assert Gate.BAD in dlg.drive_bars[0].styleSheet()
    labels = [w.text() for w in dlg.findChildren(QLabel)]
    for wanted in ("Windows version", "Slate version", "Serial number", "Signed in to Slate as",
                   "Windows account"):
        assert wanted in labels
    assert "Last report" in dlg.lbl_last.text()

    assert dlg.reload_data() is False and boxes == []
    assert dlg.lbl_note.text() == admin_widgets.NOT_REPORTED
    (hub.get_livestatus_dir() / "COMP-01.json").write_text(json.dumps(_report("COMP-01", 1)))
    assert dlg.reload_data() is True and boxes == []
    assert dlg.btn_export.property("kind") in ("secondary", None)


def test_specs_html_is_escaped():
    from slate.gui.admin_widgets import build_specs_html
    page = build_specs_html({"Model": "<b>&x", "client_version": "1.2"}, "PC<1>")
    assert "&lt;b&gt;&amp;x" in page and "PC&lt;1&gt;" in page
    assert "UT Client" not in page and "Slate version" in page


# ----------------------------------------------------------- admin panel
@pytest.fixture
def panel_for(qtbot, hub, monkeypatch):
    from slate.gui import admin_panel
    made = []

    def build(roles):
        panel = admin_panel.AdminPanelTab(current_username="tester", hub=hub, roles=roles,
                                          attendance=object())
        qtbot.addWidget(panel)
        panel.live_dashboard.auto_timer.stop()
        made.append(panel)
        return panel
    yield build
    for panel in made:
        panel.cleanup_resources()


def test_supervisor_sees_live_ops_read_only(panel_for):
    panel = panel_for(["Supervisor"])
    assert panel.page_labels() == ["Live Ops"]
    assert not hasattr(panel, "data_center") and not hasattr(panel, "unified_log_viewer")
    assert panel.live_dashboard.read_only
    assert not hasattr(panel, "inp_broadcast") and not hasattr(panel, "btn_api")
    assert panel.show_page("Data Center") is False


def test_admin_sees_every_page(panel_for):
    panel = panel_for(["Admin"])
    assert panel.page_labels() == ["Live Ops", "Audit Logs", "Data Center"]
    assert not panel.live_dashboard.read_only
    assert panel.show_page("Audit Logs") and panel.stack.currentIndex() == 1
    # Scoped sheets: no rule without a selector on the panel or the page.
    assert panel.styleSheet().strip().startswith("QWidget#AdminPanel")
    assert panel.live_dashboard.styleSheet().strip().startswith("QWidget#LiveDashboard")


def test_workstation_text_is_shown_as_plain_text(qtbot, hub):
    from PySide6.QtCore import Qt
    from slate.gui import admin_widgets
    data = _report("<b>PC</b>", 5, Model="Projects<b>", OS="<script>alert(1)</script>",
                   Drives=[{"Root": "C:", "Label": "Work<i>", "Usage": "50%"}])
    dlg = admin_widgets.PCDetailsDialog(data, hub=hub, pc_name="<b>PC</b>")
    qtbot.addWidget(dlg)
    labels = dlg.findChildren(QLabel)
    assert labels and all(l.textFormat() == Qt.TextFormat.PlainText for l in labels)
    assert "Projects<b>" in [l.text() for l in labels]
    card = admin_widgets.PCCard("<b>PC</b>", hub)
    qtbot.addWidget(card)
    card.update_data(data)
    assert all(l.textFormat() == Qt.TextFormat.PlainText for l in card.findChildren(QLabel))


@pytest.fixture(autouse=True)
def _closed_circuit_breaker():
    """
    The PostgreSQL circuit breaker is shared by every manager in the process. A
    test elsewhere that reaches for an unconfigured database opens it, and the
    tests here would then fail for two minutes for a reason that is not theirs.
    """
    from slate.core.infra.postgres_manager import PostgresManager
    PostgresManager._circuit_breaker.reset()
    yield


# ------------------------------------------------------------- round 2
def test_share_hiccup_keeps_the_cards(dashboard):
    """SYS2-001: a failed read keeps the last good read and says so."""
    now = time.time()
    dashboard.on_data_ready([_report("A", 5, now), _report("B", 5, now)], now=now)
    updated = dashboard.lbl_updated.text()
    dashboard.on_read_failed("[WinError 53] The network path was not found")
    assert set(dashboard.pc_widgets) == {"A", "B"}
    assert dashboard.summary_cards[fs.ONLINE].value_text() == "2"
    assert dashboard.lbl_updated.text() == updated
    assert not dashboard.lbl_stale.isHidden() and "last good read" in dashboard.lbl_stale.text()
    dashboard.on_data_ready([_report("A", 5, now)], now=now)
    assert dashboard.lbl_stale.isHidden()


def test_worker_reports_a_missing_folder_as_a_failure(qtbot, tmp_path):
    from slate.core.workers.admin_workers import LiveStatusWorker

    class GoneHub:
        def get_livestatus_dir(self):
            return tmp_path / "unplugged"
    worker = LiveStatusWorker(GoneHub())
    failed, ready = [], []
    worker.failed.connect(failed.append)
    worker.data_ready.connect(ready.append)
    worker.run()
    assert failed and not ready


def test_unreadable_report_stays_as_a_card(qtbot, hub):
    """SYS2-041: a broken file shows as 'Report unreadable', logged once."""
    from slate.core.workers.admin_workers import LiveStatusWorker
    folder = hub.get_livestatus_dir()
    (folder / "GOOD.json").write_text(json.dumps(_report("GOOD", 1)), encoding="utf-8")
    (folder / "BROKEN.json").write_text("{half", encoding="utf-8")
    worker = LiveStatusWorker(hub)
    got = []
    worker.data_ready.connect(got.append)
    worker.run()
    worker.run()
    names = {d["pc_name"]: d for d in got[-1]}
    assert names["BROKEN"].get("unreadable") and "_file_mtime" in names["GOOD"]
    assert len(worker._warned) == 1
    from slate.gui.admin_widgets import PCCard
    card = PCCard("BROKEN", hub)
    qtbot.addWidget(card)
    card.update_data(names["BROKEN"])
    assert card.state == fs.UNKNOWN and "unreadable" in card.lbl_status.text()


def test_file_time_beats_a_fast_clock():
    """SYS2-006: the share's file time decides freshness, not the client's clock."""
    now = 1_000_000.0
    ahead = {"last_seen": now + 3600, "_file_mtime": now - 900}
    assert fs.status_for(fs.last_seen_of(ahead), now) == fs.OFFLINE
    assert fs.last_seen_of({"last_seen": now}) == now


def test_problems_first_and_tiles_toggle(dashboard):
    """SYS2-005 / SYS2-023."""
    now = time.time()
    dashboard.on_data_ready([_report("A-OK", 5, now), _report("Z-DOWN", 900, now),
                             _report("M-QUIET", 120, now)], now=now)
    assert [c.pc_name for c in dashboard.visible_cards()] == ["Z-DOWN", "M-QUIET", "A-OK"]
    dashboard.toggle_status_filter(fs.OFFLINE)
    assert [c.pc_name for c in dashboard.visible_cards()] == ["Z-DOWN"]
    assert dashboard.summary_cards[fs.OFFLINE]._active
    dashboard.toggle_status_filter(fs.OFFLINE)
    assert len(dashboard.visible_cards()) == 3 and not dashboard.summary_cards[fs.OFFLINE]._active
    assert "report" in dashboard.summary_cards[fs.UNKNOWN].toolTip().lower()


def test_search_finds_ip_and_version(dashboard):
    """SYS2-039."""
    now = time.time()
    dashboard.on_data_ready([_report("A", 5, now, IPAddress="10.0.0.7", client_version="1.9"),
                             _report("B", 5, now, IPAddress="10.0.0.8")], now=now)
    dashboard.search.setText("10.0.0.7")
    assert [c.pc_name for c in dashboard.visible_cards()] == ["A"]
    dashboard.search.setText("1.9")
    assert [c.pc_name for c in dashboard.visible_cards()] == ["A"]
    assert dashboard.pc_widgets["A"].lbl_version.text() == "Slate 1.9"


def test_cards_share_the_row_width(dashboard):
    """SYS2-021: no empty band right of the grid."""
    now = time.time()
    dashboard.on_data_ready([_report(f"PC-{i}", 5, now) for i in range(12)], now=now)
    QApplication.processEvents()
    card = dashboard.pc_widgets["PC-0"]
    assert card.width() > card.CARD_WIDTH


def test_timer_runs_only_while_shown(dashboard):
    """SYS2-007."""
    assert dashboard.auto_timer.isActive()
    dashboard.hide()
    assert not dashboard.auto_timer.isActive()


def test_restart_is_confirmed_on_the_card(monkeypatch, qtbot, hub):
    """SYS2-009."""
    from slate.gui import admin_widgets
    monkeypatch.setattr(admin_widgets, "confirm", lambda *a, **k: True)
    monkeypatch.setattr(admin_widgets, "ask_reason", lambda *a: "")
    toasts = []
    monkeypatch.setattr(admin_widgets, "toast", lambda parent, msg, *a, **k: toasts.append(msg))
    card = admin_widgets.PCCard("COMP-01", hub, verify_callback=lambda: True)
    qtbot.addWidget(card)
    now = time.time()
    card.update_data(_report("COMP-01", 5, now))
    assert card.request_power("restart")
    assert toasts == ["Restart sent to COMP-01"] and "restart requested" in card.lbl_status.text()
    card.update_data(_report("COMP-01", -5, time.time()))
    assert "requested" not in card.lbl_status.text()


def test_remove_from_live_ops_keeps_the_file(monkeypatch, qtbot, hub):
    """SYS2-019."""
    from slate.gui import admin_widgets
    monkeypatch.setattr(admin_widgets, "confirm", lambda *a, **k: True)
    (hub.get_livestatus_dir() / "OLD-PC.json").write_text("{}", encoding="utf-8")
    removed = []
    card = admin_widgets.PCCard("OLD-PC", hub, on_removed=removed.append)
    qtbot.addWidget(card)
    assert card.remove_from_live_ops()
    assert not (hub.get_livestatus_dir() / "OLD-PC.json").exists()
    assert (hub.get_livestatus_dir() / "Retired" / "OLD-PC.json").exists() and removed == ["OLD-PC"]


def test_specs_values_and_pdf_failure(monkeypatch, qtbot, hub, tmp_path):
    """SYS2-008 / SYS2-024 / SYS2-040."""
    from slate.gui import admin_widgets
    from PySide6.QtWidgets import QMessageBox
    assert admin_widgets._value({"IPAddress": "Unknown"}, "IPAddress") == "Not reported"
    assert admin_widgets._value({"RAM_GB": "32 GB"}, "RAM_GB") == "32 GB"
    assert admin_widgets.ram_gb(32.0) == 32
    dlg = admin_widgets.PCDetailsDialog(_report("PC", 5, IPAddress="10.1.1.1"), hub=hub, pc_name="PC")
    qtbot.addWidget(dlg)
    labels = [w.text() for w in dlg.findChildren(QLabel)]
    assert "Manufacturer" not in labels             # never sent by this client
    ip = next(w for w in dlg.findChildren(QLabel) if w.text() == "10.1.1.1")
    assert "font-family" in ip.styleSheet()
    shown = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: shown.append(("warn", a[2])))
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: shown.append(("info", a[2])))
    folder = tmp_path / "ro.pdf"
    folder.mkdir()                                   # a folder: cannot be written as a file
    monkeypatch.setattr(admin_widgets.QFileDialog, "getSaveFileName", lambda *a, **k: (str(folder), ""))
    assert dlg.export_to_pdf() is False and shown[0][0] == "warn"
    good = tmp_path / "ok.pdf"
    monkeypatch.setattr(admin_widgets.QFileDialog, "getSaveFileName", lambda *a, **k: (str(good), ""))
    assert dlg.export_to_pdf() is True and good.stat().st_size > 0
