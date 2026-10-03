"""Data Center (SYS-017/018/019/045-048/067-072)."""

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from slate.gui import database_explorer as dx


@pytest.fixture
def explorer(pg_db, qtbot, monkeypatch):
    from slate.core.infra.app_context import AppContext
    ctx = AppContext(db_manager=pg_db)
    ctx.set_current_user({"username": "admin", "roles": ["Admin"]})
    pg_db.execute_update("DROP TABLE IF EXISTS dc_nokey")
    pg_db.execute_update("CREATE TABLE dc_nokey (a TEXT, b INTEGER)")
    pg_db.execute_update("INSERT INTO dc_nokey VALUES ('x', 1)")
    pg_db.execute_update("DROP TABLE IF EXISTS dc_pair")
    pg_db.execute_update("CREATE TABLE dc_pair (k1 TEXT, k2 INTEGER, user_id TEXT, note TEXT, "
                         "num INTEGER, PRIMARY KEY (k1, k2))")
    for k2 in (1, 2):
        pg_db.execute_update("INSERT INTO dc_pair VALUES ('a', %s, 'same', NULL, 5)", (k2,))
    view = dx.DatabaseExplorer(pg_db, app_context=ctx)
    qtbot.addWidget(view)
    view.resize(1280, 720)
    yield view
    view.cancel_workers()
    pg_db.execute_update("DROP TABLE IF EXISTS dc_nokey")
    pg_db.execute_update("DROP TABLE IF EXISTS dc_pair")


def _load(view, table):
    data = {"cols_res": view.db.execute_query(
        "SELECT column_name FROM information_schema.columns WHERE table_schema='public' "
        "AND table_name=%s ORDER BY ordinal_position", (table,), fetch="all"),
            "rows": view.db.execute_query(f"SELECT * FROM {table} ORDER BY 1, 2", fetch="all"),
            "keys": view.primary_key_of(table)}
    view.show_table(table, data)


def test_real_primary_key_is_used(explorer):
    assert explorer.primary_key_of("dc_pair") == ["k1", "k2"]
    assert explorer.primary_key_of("tracking_projects") in (["code"], ["id"])
    _load(explorer, "dc_pair")
    assert explorer.key_columns == ["k1", "k2"]
    assert explorer.lbl_note.isHidden()
    note = explorer.data_grid.item(0, explorer.columns.index("note"))
    assert note.text() == "" and note.data(dx.NULL_ROLE) is True


def test_table_without_key_is_read_only(explorer):
    _load(explorer, "dc_nokey")
    assert explorer.key_columns == []
    assert not explorer.lbl_note.isHidden()
    item = explorer.data_grid.item(0, 0)
    assert not (item.flags() & Qt.ItemFlag.ItemIsEditable)


def test_edit_targets_one_row_and_bad_values_revert(explorer, qtbot, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    warned = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: warned.append(a[2]))
    _load(explorer, "dc_pair")
    col = explorer.columns.index("user_id")
    explorer.data_grid.item(0, col).setText("changed")
    qtbot.waitUntil(lambda: explorer.db.execute_query(
        "SELECT COUNT(*) AS n FROM dc_pair WHERE user_id='changed'", fetch="one")["n"] == 1, timeout=5000)
    assert explorer.db.execute_query("SELECT COUNT(*) AS n FROM dc_pair WHERE user_id='same'",
                                     fetch="one")["n"] == 1
    num = explorer.columns.index("num")
    explorer.data_grid.item(0, num).setText("maybe")
    qtbot.waitUntil(lambda: bool(warned), timeout=5000)
    assert explorer.data_grid.item(0, num).text() == "5"
    history = explorer.db.execute_query(
        "SELECT * FROM change_history WHERE entity_type = 'table dc_pair'", fetch="all")
    assert history and history[0]["user_id"] == "admin"


def test_delete_names_the_row_and_is_logged(explorer, qtbot, monkeypatch):
    asked = {}

    def fake_confirm(parent, title, text, yes_label="Continue", **kw):
        asked.update(title=title, text=text, yes=yes_label, destructive=kw.get("destructive"))
        return True
    monkeypatch.setattr(dx, "confirm", fake_confirm)
    _load(explorer, "dc_pair")
    explorer.delete_row(1)
    assert "Dc pair" in asked["title"] and "k1=a, k2=2" in asked["text"] and asked["destructive"]
    qtbot.waitUntil(lambda: explorer.data_grid.rowCount() == 1, timeout=5000)
    assert explorer.db.execute_query("SELECT COUNT(*) AS n FROM dc_pair", fetch="one")["n"] == 1
    rows = explorer.db.execute_query(
        "SELECT * FROM change_history WHERE action_type='DELETE' AND entity_type='table dc_pair'",
        fetch="all")
    assert rows and rows[0]["entity_id"] == "k1=a, k2=2"


def test_purge_needs_the_typed_word(explorer, qtbot, monkeypatch):
    dlg = dx.MaintenanceDialog()
    qtbot.addWidget(dlg)
    assert not dlg.btn_purge.isEnabled()
    dlg.confirm_input.setText("purge")
    assert not dlg.btn_purge.isEnabled()
    dlg.confirm_input.setText("PURGE")
    assert dlg.btn_purge.isEnabled() and dlg.confirmed()

    statements = []
    real = explorer.db.execute_update
    monkeypatch.setattr(explorer.db, "execute_update", lambda sql, *a, **k: statements.append(sql) or real(sql, *a, **k))
    monkeypatch.setattr(dx.MaintenanceDialog, "exec", lambda self: 0)
    explorer.purge_stock_library()                  # not confirmed: nothing runs
    assert not any("stock_library" in s for s in statements)
    explorer.purge_stock_library(confirmed=True)
    qtbot.waitUntil(lambda: any("TRUNCATE" in s for s in statements), timeout=5000)
    assert all("CASCADE" not in s for s in statements if "TRUNCATE" in s)


def test_role_counts_and_stats(explorer, pg_db):
    assert dx.count_roles([{"roles": '["Artist"]'}, {"roles": '["Artist", "Lead"]'}, {"roles": ""}]) \
        == {"Artist": 2, "Lead": 1, "Unassigned": 1}
    from slate.core.domain.user_manager import UserManager
    um = UserManager()
    for name, last in (("dc_a", None), ("dc_b", None), ("dc_gone", "2000-01-01")):
        um.add_user(name, "pw-123456", ["Artist"], name, "Artist")
        if last:
            pg_db.execute_update("UPDATE ut_users SET last_day=%s WHERE username=%s", (last, name))
    stats = explorer.dashboard_view.fetch_stats()
    assert stats["count_users"] == len(um.active_users()) >= 2
    assert "dc_gone" not in str(stats)
    explorer.dashboard_view.resize(900, 600)
    explorer.dashboard_view.show_stats(stats)
    assert explorer.dashboard_view.card_columns() == 2
    positions = [explorer.dashboard_view.stats_layout.getItemPosition(
        explorer.dashboard_view.stats_layout.indexOf(c))[:2] for c in explorer.dashboard_view.cards]
    assert positions == [(0, 0), (0, 1), (1, 0), (1, 1)]


def test_sidebar_and_console_look(explorer):
    assert explorer.left_widget.styleSheet().startswith("QWidget#DcSidebar")
    assert explorer.left_widget.maximumWidth() > 220
    assert explorer.btn_run.text() == "Run" and "Ctrl+Enter" in explorer.btn_run.toolTip()
    explorer.show_tables([{"table_name": "ut_users"}, {"table_name": "stock_library"}])
    labels = {explorer.table_list.item(i).text(): explorer.table_list.item(i).toolTip()
              for i in range(explorer.table_list.count())}
    assert labels.pop("Overview")
    assert labels == {"Users": "ut_users", "Stock library": "stock_library"}
    import pathlib, re
    source = pathlib.Path(dx.__file__).read_text(encoding="utf-8")
    assert not re.search(r"[\U0001F300-\U0001FAFF❌]", source)


def test_ctrl_enter_runs_the_sql(explorer, qtbot, monkeypatch):
    ran = []
    monkeypatch.setattr(dx.DatabaseExplorer, "run_custom_sql", lambda self: ran.append(1))
    view = dx.DatabaseExplorer(explorer.db, app_context=explorer.app_context)
    qtbot.addWidget(view)
    view.show()
    view.table_view_widget.show()
    view.txt_sql.setFocus()
    QApplication.processEvents()
    qtbot.keyClick(view.txt_sql, Qt.Key.Key_Return, Qt.KeyboardModifier.ControlModifier)
    assert ran


def test_bar_value_label_sits_above_the_bar():
    rect = dx.SimpleBarChart.value_label_rect(100, 150, 40, 50)
    assert rect.bottom() <= 150
    top = dx.SimpleBarChart.value_label_rect(100, 50, 40, 50)
    assert top.top() >= 34


def test_table_list_groups_system_tables(explorer):
    """SYS2-030 / SYS2-032 / SYS2-037."""
    explorer.show_tables([{"table_name": n} for n in
                          ("ut_users", "stock_favorites", "it_licenses", "ut_role_seeds")])
    items = [explorer.table_list.item(i) for i in range(explorer.table_list.count())]
    texts = [i.text() for i in items]
    assert texts[0] == "Overview" and explorer.table_list.currentItem() is items[0]
    assert "Stock favourites" in texts and "▸ System tables (2)" in texts
    system = [i for i in items if i.data(dx.TABLE_ROLE) in dx.SYSTEM_TABLES]
    assert system and all(i.isHidden() for i in system)
    header = next(i for i in items if i.data(dx.TABLE_ROLE) == dx.SYSTEM_HEADER)
    explorer._on_list_clicked(header)
    assert not any(i.isHidden() for i in system)
    assert "padding" in explorer.table_list.styleSheet()


def test_table_view_counts_rows_and_formats_times(explorer):
    """SYS2-018: row count, Refresh, formatted timestamps (raw in the editor)."""
    from datetime import datetime
    data = {"cols_res": [{"column_name": "id"}, {"column_name": "seen"}],
            "rows": [{"id": 1, "seen": datetime(2026, 10, 3, 16, 41, 5, 123456)}], "keys": ["id"]}
    explorer.show_table("dc_pair", data)
    assert explorer.lbl_rows.text() == "1 row" and explorer.btn_reload.text() == "Refresh"
    index = explorer.data_grid.model().index(0, 1)
    from PySide6.QtWidgets import QStyleOptionViewItem
    option = QStyleOptionViewItem()
    explorer.data_grid.itemDelegate().initStyleOption(option, index)
    assert option.text == "3 Oct 2026, 16:41:05"
    assert "123456" in explorer.data_grid.item(0, 1).text()


def test_sql_result_clears_the_no_key_note(explorer, qtbot):
    """SYS2-015 / SYS2-034: the note goes; the SQL box is under both views."""
    _load(explorer, "dc_nokey")
    assert not explorer.lbl_note.isHidden()
    explorer.txt_sql.setPlainText("SELECT 1 AS one")
    explorer.run_custom_sql()
    qtbot.waitUntil(lambda: explorer.lbl_table_name.text().startswith("SQL Result"), timeout=5000)
    assert explorer.lbl_note.isHidden()
    assert explorer.sql_box.parent() is explorer.right_split


def test_overview_tones_and_purge_placeholder(explorer):
    """SYS2-031 / SYS2-033."""
    from slate.core.infra.gate import Gate
    explorer.dashboard_view.show_stats({"count_assets": 1, "count_users": 2, "count_projects_lineup": 0,
                                        "count_projects_tracking": 0, "res_types": [],
                                        "roles": {"Artist": 2}})
    users = explorer.dashboard_view.cards[1]
    assert users._tone == Gate.INFO
    dlg = dx.MaintenanceDialog()
    assert dlg.confirm_input.placeholderText() != dlg.WORD


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
