"""
Audit Logs (SYS-003/005/006/007/013/014/038/039/040/059-064/066).
"""

import json
from datetime import datetime, timedelta

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from slate.core.infra.gate import Gate
from slate.gui import advanced_log_viewer as alv


# ------------------------------------------------------------ parsing
@pytest.mark.parametrize("line, level, source, msg", [
    ("2026-09-30 11:00:00,000 [ERROR] slate.x: boom", "ERROR", "slate.x", "boom"),
    ("2026-09-30 11:00:00,000 ERROR root: Failed to open", "ERROR", "root", "Failed to open"),
    ("2026-09-30 11:00:00,000 - ERROR - disk gone", "ERROR", "", "disk gone"),
    ("2026-09-30 11:00:00,000 - slate.db - WARNING - slow", "WARNING", "slate.db", "slow"),
    ("2026-09-30 11:00:00,000 [WARN] a: b", "WARNING", "a", "b"),
    ("2026-09-30 11:00:00,000 something without a level", "", "", "something without a level"),
])
def test_levels_are_read_in_every_format(line, level, source, msg):
    entry = alv.parse_log_line(line)
    assert (entry["level"], entry["source"], entry["msg"]) == (level, source, msg)


def test_tracebacks_stay_with_their_line():
    text = ("2026-09-30 11:00:00,000 [ERROR] slate.x: boom\n"
            "Traceback (most recent call last):\n"
            "  File \"x.py\", line 1\n"
            "PermissionError: [WinError 5] Access is denied\n"
            "2026-09-30 11:00:01,000 [INFO] slate.x: next\n")
    entries, dates, capped = alv.parse_log_text(text)
    assert len(entries) == 2 and dates == {"2026-09-30"} and not capped
    assert entries[0]["msg"] == "boom" and "PermissionError" in entries[0]["detail"]
    assert "permissions" in entries[0]["explanation"]
    assert entries[1]["explanation"] == ""


def test_explanations_are_plain_and_never_guess():
    assert alv.LogInterpreter.explain("No critical issues found in scan") == ""
    assert alv.LogInterpreter.explain("INFO Warning: x") == ""
    assert "permissions" in alv.LogInterpreter.explain("PermissionError: [WinError 5]")
    for _pattern, sentence in alv.LogInterpreter.RULES:
        assert "[" not in sentence and "ℹ" not in sentence


def test_machine_label():
    assert alv.machine_label("COMP-03_rahul") == "COMP-03 · rahul"
    assert alv.machine_label("SOLO") == "SOLO"


# -------------------------------------------------------- workstation logs
def _write_log(path, n_errors=6, n_info=20):
    lines = []
    for i in range(n_info):
        lines.append(f"2026-09-30 10:{i:02d}:00,000 [INFO] slate.app: step {i} disk check")
    for i in range(n_errors):
        lines.append(f"2026-09-30 11:{i:02d}:00,000 [ERROR] slate.app: failure {i}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


@pytest.fixture
def sys_viewer(qtbot, tmp_path):
    root = tmp_path / "Logs"
    root.mkdir()
    _write_log(root / "COMP-03_rahul.log")
    viewer = alv.SystemLogViewer(log_root=root)
    viewer.refresh_timer.stop()
    qtbot.addWidget(viewer)
    viewer.resize(1400, 500)
    viewer.show()
    yield viewer
    viewer.cleanup_resources()


def test_workstation_list_and_filters(sys_viewer):
    item = sys_viewer.list_widget.item(0)
    assert item.text().startswith("COMP-03 · rahul")
    assert sys_viewer.right_layout.contentsMargins().left() >= 8
    sys_viewer.load_log_file(item)
    assert sys_viewer.lbl_info.text() == "Showing the whole log."
    assert sys_viewer.log_table.rowCount() == 26
    sys_viewer.level_filter.setCurrentIndex(2)      # errors only
    assert sys_viewer.log_table.rowCount() == 6
    sys_viewer.level_filter.setCurrentIndex(0)
    sys_viewer.search.setText("disk")
    assert sys_viewer.log_table.rowCount() == 20
    assert sys_viewer.log_table.isSortingEnabled()


def test_missing_log_folder_shows_an_empty_state(qtbot, tmp_path):
    viewer = alv.SystemLogViewer(log_root=tmp_path / "nowhere")
    viewer.refresh_timer.stop()
    qtbot.addWidget(viewer)
    viewer.show()
    assert viewer.list_widget.count() == 0 and viewer.list_empty.isVisible()
    viewer.cleanup_resources()


def test_auto_refresh_keeps_selection_and_scroll(sys_viewer, qtbot):
    sys_viewer.load_log_file(sys_viewer.list_widget.item(0))
    table = sys_viewer.log_table
    table.selectRow(3)
    key = table.item(3, 0).data(alv.Qt.ItemDataRole.UserRole + 51)
    table.verticalScrollBar().setValue(0)
    calls = []
    original = sys_viewer.populate_table
    sys_viewer.populate_table = lambda *a: (calls.append(1), original())

    sys_viewer.auto_refresh()                       # unchanged file: nothing happens
    assert calls == []

    path = sys_viewer.current_file
    with open(path, "a", encoding="utf-8") as f:
        f.write("2026-09-30 12:00:00,000 [INFO] slate.app: appended\n")
    sys_viewer._stamp = None
    sys_viewer.auto_refresh()
    qtbot.waitUntil(lambda: bool(calls), timeout=5000)
    rows = alv.selected_rows(table)
    assert len(rows) == 1 and table.item(rows[0], 0).data(alv.Qt.ItemDataRole.UserRole + 51) == key
    assert table.verticalScrollBar().value() == 0


def test_detail_pane_shows_the_whole_entry(qtbot, tmp_path):
    root = tmp_path / "Logs"
    root.mkdir()
    (root / "PC_a.log").write_text(
        "2026-09-30 11:00:00,000 [ERROR] slate.x: boom\nTraceback line\n", encoding="utf-8")
    viewer = alv.SystemLogViewer(log_root=root)
    viewer.refresh_timer.stop()
    qtbot.addWidget(viewer)
    viewer.load_log_file(viewer.list_widget.item(0))
    viewer.log_table.selectRow(0)
    assert "Traceback line" in viewer.detail.toPlainText()
    viewer.cleanup_resources()


# ---------------------------------------------------------- change history
def test_descriptions_never_show_none():
    assert alv.describe_change({"action_type": "CREATE", "entity_type": "shot",
                                "entity_id": "SH012"}) == "Created shot SH012"
    text = alv.describe_change({"action_type": "UPDATE", "field_changed": "None",
                                "old_value": "None", "new_value": None})
    assert "None" not in text
    assert alv.describe_change({"action_type": "UPDATE", "field_changed": "status",
                                "old_value": "In Progress", "new_value": "Approved"}) \
        == "status: In Progress → Approved"
    assert alv.item_label({"shot_name": "SH012", "department": "comp"}) == "SH012 · comp"
    assert alv.action_colour("DELETE") == Gate.BAD
    assert alv.action_colour("UPDATE", "roles") == Gate.WARN


class FakeHistoryDb:
    def __init__(self, n):
        base = datetime(2026, 9, 1, 12, 0, 0)
        self.rows = [{"timestamp": base + timedelta(minutes=i), "user_name": "System Admin",
                      "display_name": "System Admin", "project_code": "KLC",
                      "action_type": "UPDATE", "field_changed": "status",
                      "old_value": "WIP", "new_value": f"v{i}", "entity_type": "task",
                      "entity_id": f"SH{i:03d}_comp", "shot_name": f"SH{i:03d}",
                      "department": "comp"} for i in range(n)]
        self.rows.reverse()

    def get_history(self, limit=200, offset=0, since=None, until=None, **_):
        rows = self.rows
        if since is not None:
            rows = [r for r in rows if since <= r["timestamp"] < until]
        return rows[offset:offset + limit]


def test_change_history_pages_and_counts(qtbot, monkeypatch):
    db = FakeHistoryDb(2500)
    monkeypatch.setattr(alv.DatabaseAuditViewer, "_count", lambda self: len(db.rows))
    viewer = alv.DatabaseAuditViewer(db)
    qtbot.addWidget(viewer)
    assert viewer.table.rowCount() == 2000
    assert "of 2,500" in viewer.lbl_count.text() and viewer.btn_more.isVisibleTo(viewer)
    viewer.load_more()
    assert viewer.table.rowCount() == 2500
    headers = [viewer.table.horizontalHeaderItem(c).text() for c in range(viewer.table.columnCount())]
    assert headers == ["Time", "User", "Project", "Item", "Action", "Description", "What it means"]
    assert viewer.table.item(0, 1).text() == "System Admin"
    assert viewer.table.item(0, 2).text() == "KLC"
    assert viewer.table.item(0, 3).text() == "SH2499 · comp"


def test_change_history_search_is_debounced(qtbot):
    viewer = alv.DatabaseAuditViewer(FakeHistoryDb(30))
    qtbot.addWidget(viewer)
    calls = []
    original = viewer.populate_table
    viewer._search_timer.timeout.disconnect()
    viewer._search_timer.timeout.connect(lambda: (calls.append(1), original()))
    for ch in "SH00":
        viewer.search.setText(viewer.search.text() + ch)
    assert calls == []
    qtbot.waitUntil(lambda: bool(calls), timeout=2000)
    assert len(calls) == 1 and viewer.table.rowCount() == 10


def test_change_history_reads_the_real_database(pg_db, qtbot):
    pg_db.log_change_event("AUDT", "shot", "SH012", "admin", "CREATE", None, None, None)
    pg_db.log_change_event("AUDT", "task", "SH012_comp", "admin", "UPDATE", "status", None, None)
    viewer = alv.DatabaseAuditViewer(pg_db)
    qtbot.addWidget(viewer)
    viewer.search.setText("AUDT")
    viewer.populate_table()
    texts = [viewer.table.item(r, 5).text() for r in range(viewer.table.rowCount())]
    assert "Created shot SH012" in texts
    assert all("None" not in t for t in texts)
    users = {viewer.table.item(r, 1).text() for r in range(viewer.table.rowCount())}
    assert "Unknown" not in users
    viewer.search.setText("admin")
    viewer.populate_table()
    assert viewer.table.rowCount() > 0
    from slate.core.infra.change_history import count_history
    assert count_history(getattr(pg_db, "backend", pg_db)) >= 2


# ------------------------------------------------------------- audit trail
def test_audit_trail_reads_both_sources(qtbot, tmp_path):
    audit_dir = tmp_path / "Logs" / "Audit"
    audit_dir.mkdir(parents=True)
    (audit_dir / "audit_2026-09-30.log").write_text(json.dumps({
        "timestamp": "2026-09-30T10:00:00", "type": "AUTH", "user": "rahul",
        "status": "FAILURE", "details": "Login failed: bad password"}) + "\n", encoding="utf-8")
    legacy = tmp_path / "Config" / "audit.log"
    legacy.parent.mkdir()
    legacy.write_text("[2026-09-30 11:00:00] admin: Broadcast Alert: hello\n", encoding="utf-8")
    entries = alv.read_audit_trail(audit_dir, legacy)
    assert [e["type"] for e in entries] == ["ADMIN", "AUTH"]
    viewer = alv.AuditTrailViewer(audit_dir, legacy)
    qtbot.addWidget(viewer)
    assert viewer.table.rowCount() == 2
    viewer.chk_failures.setChecked(True)
    assert viewer.table.rowCount() == 1


def test_unified_viewer_names_its_tabs(qtbot, tmp_path):
    viewer = alv.UnifiedLogViewer(db_manager=FakeHistoryDb(3), audit_dir=tmp_path,
                                  audit_file=tmp_path / "audit.log", log_root=tmp_path)
    qtbot.addWidget(viewer)
    names = [viewer.tabs.tabText(i) for i in range(viewer.tabs.count())]
    assert names == ["Workstation logs", "Change history", "Audit trail"]
    viewer.cleanup_resources()


def test_role_changes_show_permission_names_not_keys(qtbot, tmp_path):
    """MED-108 note: the trail showed raw keys ('Shot Review') for role changes."""
    import json as _json
    from slate.core.domain.permissions_catalog import describe_role_change, permission_label
    from slate.gui.advanced_log_viewer import AuditTrailViewer
    assert permission_label("Shot Review") == "Timeline Viewer"
    assert permission_label("can:approve_leave").startswith("Approve leave")
    assert permission_label("can:department_scoped").startswith("Only")
    assert permission_label("ALL") == "Everything" and permission_label("odd") == "odd"
    assert describe_role_change("Deleted role X") == "Deleted role X"
    (tmp_path / "audit_2026-10-02.log").write_text(_json.dumps({
        "timestamp": "2026-10-02T10:00:00", "user": "admin", "type": "ROLE_MGMT", "status": "SUCCESS",
        "details": "Changed role Lead: added Shot Review, Stock Browser; removed nothing"}) + "\n",
        encoding="utf-8")
    viewer = AuditTrailViewer(str(tmp_path))
    qtbot.addWidget(viewer)
    viewer.refresh_data()
    texts = [viewer.table.item(r, c).text() for r in range(viewer.table.rowCount())
             for c in range(viewer.table.columnCount()) if viewer.table.item(r, c)]
    assert any("added Timeline Viewer, Stock Viewer; removed nothing" in t for t in texts)
    assert not any("Shot Review" in t for t in texts)


def test_change_history_count_wording(qtbot, monkeypatch):
    db = FakeHistoryDb(2128)
    monkeypatch.setattr(alv.DatabaseAuditViewer, "_count", lambda self: len(db.rows))
    viewer = alv.DatabaseAuditViewer(db)
    qtbot.addWidget(viewer)
    assert viewer.lbl_count.text() == "Showing the newest 2,000 of 2,128 changes. Load more for older ones."
    viewer.search.setText("SH2127")
    viewer.populate_table()
    assert viewer.lbl_count.text().startswith("Showing 1 of the newest 2,000 changes (2,128 in all).")
    viewer.load_more()
    viewer.search.setText("")
    viewer.populate_table()
    assert viewer.lbl_count.text() == "Showing all 2,128 changes."
