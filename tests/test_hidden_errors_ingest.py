"""
Hidden errors, ingest track (hardening 2.2.1): a failure must not look like
success or like "there is nothing". One test per rule.
"""

import logging
from pathlib import Path

import pytest

from slate.core.infra.db_results import DatabaseReadError


class StrictFails:
    """A database whose strict reads are refused."""

    def execute_query(self, *args, strict=False, **kwargs):
        if strict:
            raise DatabaseReadError("relation is locked")
        return None


# 1 ----------------------------------------------------------------- re-ingest

def test_a_failed_known_paths_read_stops_the_ingest_before_anything_is_written(
        monkeypatch, tmp_path):
    from slate.core.domain import asset_ingestor
    from slate.core.infra.stock_repository import StockRepository

    with pytest.raises(DatabaseReadError):
        StockRepository(StrictFails()).list_stock_paths()

    written = []

    class Library:
        def list_known_paths(self):
            return StockRepository(StrictFails()).list_stock_paths()

        def add_assets_batch(self, assets):
            written.append(assets)

        def remember_root(self, root):
            written.append(root)

    (tmp_path / "plate.jpg").write_bytes(b"jpeg")
    monkeypatch.setattr(asset_ingestor, "create_asset_api", lambda **kw: Library())
    worker = asset_ingestor.IngestWorker(root_path=tmp_path)
    finished, summaries = [], []
    worker.finished_signal.connect(lambda ok, msg: finished.append((ok, msg)))
    worker.summary_ready.connect(summaries.append)
    worker.run()

    assert written == []
    assert finished and finished[-1][0] is False
    from slate.gui.tabs.stock_browser.controllers.ingest_controller import summary_sentence
    text, level = summary_sentence(summaries[-1])
    assert level == "error" and "Nothing was added" in text


# 2 ----------------------------------------------------- analysis not stored

def test_analysed_assets_that_were_not_saved_are_failed_and_not_shown(monkeypatch, tmp_path):
    from slate.core.domain import asset_ingestor

    class Library:
        def update_assets_batch(self, assets):
            raise RuntimeError("database refused")

    monkeypatch.setattr(asset_ingestor, "create_asset_api", lambda **kw: Library())
    worker = asset_ingestor.IngestWorker(root_path=tmp_path)
    shown = []
    worker.assets_update_batch_signal.connect(shown.append)
    worker._update_buffer = [({"file_name": "a.jpg", "status": "ready"}, False)]
    worker._flush_update_buffer()

    assert shown == []
    assert worker.summary["failed"] == 1 and worker.summary["added"] == 0
    assert worker.summary["failed_names"] == ["a.jpg (not saved)"]


# 3 ------------------------------------------------ local-only picture cache

def test_a_local_only_cache_is_flagged_and_its_paths_are_not_shared(monkeypatch, tmp_path):
    from slate.core.domain import proxy_manager as pm_module
    from slate.core.domain.library_manager import LibraryManager
    from slate.core.infra.global_config import GlobalConfig

    blocked = tmp_path / "server_is_a_file"
    blocked.write_text("")
    monkeypatch.setattr(GlobalConfig, "server_root", classmethod(lambda cls: blocked))
    monkeypatch.setattr(GlobalConfig, "local_cache_dir", classmethod(lambda cls: tmp_path / "local"))
    manager = pm_module.ProxyManager.__new__(pm_module.ProxyManager)
    assert manager._get_cache_dir() == tmp_path / "local"
    assert manager.local_only is True

    monkeypatch.setattr(pm_module.proxy_manager, "local_only", True)
    data = LibraryManager._shareable({"file_path": "x", "thumb_path": "C:/local/t.jpg",
                                      "proxy_path": "C:/local/p.mp4", "tags": ["a"]})
    assert data == {"file_path": "x", "tags": ["a"]}


# 5 ------------------------------------------------------ permission cache

def test_a_failed_role_read_keeps_the_last_answer_and_is_never_cached(monkeypatch):
    from types import SimpleNamespace
    from slate.core.domain import access
    from slate.core.infra import database_manager as dbm

    # The module attribute is swapped, never the shared proxy's methods: a
    # patched proxy stays pinned to this test's database afterwards.
    answer = [{"role_name": "Lead", "permissions": '["can:dashboard_write"]'}]
    monkeypatch.setattr(dbm, "database_manager",
                        SimpleNamespace(execute_query=lambda *a, **k: answer))
    access.reset_cache()
    try:
        good = access._role_abilities()
        assert "dashboard_write" in good["lead"]

        monkeypatch.setattr(dbm, "database_manager", StrictFails())
        monkeypatch.setattr(access, "_db_cache_at", 0.0)          # expired
        assert access._role_abilities() == good                  # last answer kept

        access.reset_cache()
        assert access._role_abilities() == {}                    # as before: no ticks
        assert access._db_cache is None                          # but not cached
        assert access._role_permission_lists() == {}
        assert access._perm_cache is None
    finally:
        access.reset_cache()


# 6 ------------------------------------------------- dashboard rows not shown

def test_a_shot_that_cannot_be_read_is_reported_not_dropped_silently():
    from slate.gui.tabs.vfx_dashboard_pro.core.sqlite_handler import SQLiteHandler

    class Db:
        def execute_query(self, sql, params=None, fetch="all", **kw):
            if "FROM tracking_shots" in sql:
                return [{"id": 1, "reel": "R1", "shot_name": "SH010",
                         "data_json": "{broken", "version": 1}]
            return []

    handler = SQLiteHandler("PRJ", db_manager=Db())
    assert handler.read_shots() == []
    assert len(handler.read_problems) == 1 and handler.read_problems[0].startswith("SH010:")


# 7 ------------------------------------------------- restore with bad config

def test_a_project_whose_settings_cannot_be_read_stays_archived(monkeypatch):
    from types import SimpleNamespace
    from slate.gui.tabs.vfx_dashboard_pro.core.project_manager import ProjectManager
    from slate.core.infra import database_manager as dbm

    def unreadable(code):
        raise ValueError("bad json")

    updates = []
    monkeypatch.setattr(ProjectManager, "archived_projects", staticmethod(lambda: [{"code": "PRJ"}]))
    monkeypatch.setattr(dbm, "database_manager", SimpleNamespace(
        get_tracking_project=unreadable,
        execute_update=lambda *a, **k: updates.append(a)))
    pm = ProjectManager.__new__(ProjectManager)
    pm.projects, pm.last_error = {}, ""

    assert pm.restore_project("PRJ") is False
    assert updates == [] and "PRJ" not in pm.projects
    assert "stays archived" in pm.last_error


# 8 ----------------------------------------------------- workstation backup

def test_a_skipped_workstation_backup_reaches_the_audit_log(monkeypatch, tmp_path, caplog):
    from slate.core.domain import backup_service
    from slate.core.infra import audit_logger, local_secrets

    recorded = []
    monkeypatch.setattr(local_secrets, "db_settings", lambda: {"password": ""})
    monkeypatch.setattr(audit_logger.AuditLogger, "log_event",
                        lambda self, kind, user, details, status="SUCCESS":
                        recorded.append((kind, status, details)))
    with caplog.at_level(logging.WARNING):
        assert backup_service.take_backup(Path("pg_dump.exe"), tmp_path) is None
    assert recorded and recorded[0][:2] == ("BACKUP", "FAILURE")
    assert "no database password" in caplog.text


# 9 ------------------------------------------------------ free space unknown

def test_unknown_free_space_is_said_not_assumed(monkeypatch):
    from slate.core.workers import structure

    def broken(path):
        raise OSError("drive not ready")

    monkeypatch.setattr(structure.psutil, "disk_usage", broken)
    assert structure.free_space("Z:/") is None

    worker = structure.FolderCreationWorker(target_dir=".", dry_run=True)
    said = []
    worker.log_signal.connect(said.append)
    assert worker._enough_space(10, "Z:/") == (True, "")
    assert said and "unknown" in said[0]

    from slate.core.infra.file_operations import SafeFileOperations
    import slate.core.infra.file_operations as fo
    monkeypatch.setattr(fo.psutil, "disk_usage", broken)
    assert SafeFileOperations._check_disk_space(10, Path("Z:/")) == (True, "Free space unknown")
