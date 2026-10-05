"""
Features that looked as if they worked but ended in nothing.

One check per finding of the "never worked" sweep (done_realtools_dead.md).
"""
import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_every_module_parses():
    """
    The Slate Server's main.py and the dashboard's Advanced filter dialog did
    not compile (a newline and two quotes lost in an edit), so the server
    could not start from source and the filter dialog could not open. No test
    imported either file.
    """
    broken = []
    for top in ("slate", "slate_server"):
        for path in (ROOT / top).rglob("*.py"):
            try:
                ast.parse(path.read_bytes(), filename=str(path))
            except SyntaxError as exc:
                broken.append(f"{path.relative_to(ROOT)}:{exc.lineno} {exc.msg}")
    for path in (ROOT / "slate_recover.py",):
        ast.parse(path.read_bytes())
    assert not broken, broken


def test_dashboard_retry_reconnects(monkeypatch):
    """The offline banner's Retry asked for a reconnect() nothing has, so it never retried."""
    from slate.core.domain import access
    from slate.gui.tabs.vfx_dashboard_pro.ui import dashboard_widget as module

    calls = []

    class FakeManager:
        def reload_from_config(self):
            calls.append("reload")

    class FakeDashboard:
        def refresh_connection_state(self):
            return False

        def _notify(self, text, level, **_):
            calls.append(level)

        def refresh_data(self):
            calls.append("refresh")

    monkeypatch.setattr(module, "database_manager", FakeManager())
    monkeypatch.setattr(access, "is_offline_fallback", lambda: True)
    module.DashboardWidget.retry_connection(FakeDashboard())
    assert calls == ["reload", "success", "refresh"]
