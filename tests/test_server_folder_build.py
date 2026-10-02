"""
The server is a folder, not a single file that unpacks into %TEMP%.

A studio cleaned its temp folder while Slate Server was running - routine
housekeeping - and the server failed. The one-file build had unpacked
PostgreSQL, PgBouncer, Qt and its own settings into %TEMP%\\_MEIxxxx at start
and was running from there. The clients were already folders; the server now
is too, and nothing Slate needs lives in a folder that gets cleaned.

Alongside: the two things learned from a 17,000-asset ingest that took the
program to 100 GB. The ingest now writes its own memory use to the log, and
the gallery no longer decodes full-size source pictures for cards whose
thumbnail the ingest is about to produce.
"""

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
DEPLOY = ROOT / "deployment"
sys.path.insert(0, str(ROOT / "tools"))


def read(path):
    return (DEPLOY / path).read_text(encoding="utf-8")


# ------------------------------------------------------------- the build

class TestTheServerIsBuiltAsAFolder:

    def test_the_spec_collects_into_a_folder(self):
        spec = read("Slate_Server.spec")
        assert "COLLECT(" in spec
        assert "exclude_binaries=True" in spec
        assert "runtime_tmpdir" not in spec, "that setting only exists for one-file builds"
        assert "name='Slate_Server'" in spec

    def test_the_settings_and_postgres_still_travel_with_it(self):
        spec = read("Slate_Server.spec")
        assert "(R('slate', 'default_config.json'), 'slate')" in spec
        assert "prefix='slate_server/bin'" in spec

    def test_a_previous_postgres_kept_beside_the_current_one_is_not_shipped(self):
        spec = read("Slate_Server.spec")
        excludes = spec.split("prefix='slate_server/bin'")[1].split("]")[0]
        assert "'pgsql.*'" in excludes, "pgsql.14 beside pgsql would double the server"

    def test_the_pipeline_looks_for_the_folder(self):
        pipeline = (ROOT / "tools" / "build_pipeline.py").read_text(encoding="utf-8")
        assert 'os.path.join("dist", "Slate_Server")' in pipeline
        assert "stamp_before" in pipeline and "was not rewritten" in pipeline

    def test_the_update_package_carries_the_whole_folder(self):
        tool = (ROOT / "tools" / "build_update_package.py").read_text(encoding="utf-8")
        assert 'project_root / "dist" / "Slate_Server"' in tool
        assert "shutil.copytree(server_dir, dist_dir)" in tool
        assert 'shutil.copy2(server_exe, dist_dir / "Slate_Server.exe")' not in tool


class TestTheSettingsCheckReadsTheFolder:

    @pytest.fixture
    def check(self):
        import build_pipeline
        return build_pipeline._check_server_carries_its_settings

    @staticmethod
    def _folder(tmp_path, config):
        folder = tmp_path / "Slate_Server"
        (folder / "_internal" / "slate").mkdir(parents=True)
        (folder / "Slate_Server.exe").write_bytes(b"bootloader")
        if config is not None:
            (folder / "_internal" / "slate" / "default_config.json").write_text(
                json.dumps(config), encoding="utf-8")
        return folder

    def test_a_complete_build_passes(self, check, tmp_path, capsys):
        folder = self._folder(tmp_path, {"db_password": "x", "db_name": "ut_vfx", "db_user": "slate"})
        check(str(folder))
        assert "settings bundled" in capsys.readouterr().out

    def test_it_accepts_the_executable_path_too(self, check, tmp_path, capsys):
        folder = self._folder(tmp_path, {"db_password": "x", "db_name": "ut_vfx", "db_user": "slate"})
        check(str(folder / "Slate_Server.exe"))
        assert "settings bundled" in capsys.readouterr().out

    def test_a_build_with_no_settings_is_refused(self, check, tmp_path):
        folder = self._folder(tmp_path, None)
        with pytest.raises(SystemExit):
            check(str(folder))

    def test_a_build_with_no_password_is_refused(self, check, tmp_path):
        folder = self._folder(tmp_path, {"db_name": "ut_vfx"})
        with pytest.raises(SystemExit):
            check(str(folder))


# --------------------------------------------------------- the installer

class TestTheServerInstallerShipsTheFolder:

    def test_it_copies_the_folder_recursively(self):
        iss = read("setup_slate_server.iss")
        line = next(l for l in iss.splitlines() if l.startswith("Source:") and "Slate_Server" in l)
        assert "\\Slate_Server\\*" in line
        assert "recursesubdirs" in line and "createallsubdirs" in line

    def test_an_upgrade_replaces_internal_whole(self):
        iss = read("setup_slate_server.iss")
        install_delete = iss.split("[InstallDelete]")[1].split("[")[0]
        assert 'Type: filesandordirs; Name: "{app}\\_internal"' in install_delete

    def test_the_updater_still_sits_beside_the_executable(self):
        iss = read("setup_slate_server.iss")
        assert 'Source: "{#SourceDistDir}\\Slate\\SlateUpdater.exe"; DestDir: "{app}"' in iss

    def test_the_server_finds_postgres_where_a_folder_build_puts_it(self):
        """
        In a folder build sys._MEIPASS is the _internal folder, and the Tree in
        the spec lands under it as slate_server/bin. The engines read it from
        exactly there.
        """
        for module in ("db_engine.py", "pgbouncer_engine.py"):
            source = (ROOT / "slate_server" / "core" / module).read_text(encoding="utf-8")
            assert "_MEIPASS" in source and '"slate_server"' in source, module


# ------------------------------------------------------- large libraries

class TestALargeIngestExplainsItsMemory:

    def test_the_memory_line_reads_like_a_measurement(self):
        from slate.core.domain.asset_ingestor import describe_memory
        line = describe_memory()
        assert "memory" in line
        assert "MB" in line or line == "memory unknown"

    def test_it_is_written_as_the_analysis_goes(self):
        source = (ROOT / "slate" / "core" / "domain" / "asset_ingestor.py").read_text(encoding="utf-8")
        assert "describe_memory()" in source
        assert "% 250 == 0" in source


class TestTheGalleryWaitsForTheIngestsThumbnail:

    @pytest.fixture
    def model(self):
        import os
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication
        if QApplication.instance() is None:
            QApplication([])
        from slate.gui.stock_model import StockModel
        m = StockModel()
        yield m
        m.loader.stop()

    @staticmethod
    def _decoration(model, asset):
        from PySide6.QtCore import Qt
        model.load_data([asset])
        return model.data(model.index(0, 0), Qt.ItemDataRole.DecorationRole)

    def test_a_still_being_ingested_does_not_send_its_source_to_the_loader(self, model):
        asked = []
        model.loader.request_image = asked.append
        self._decoration(model, {"id": "a", "name": "plate.tif", "path": "Z:/stock/plate.tif",
                                 "thumb_path": None, "status": "ingesting"})
        assert asked == []

    def test_a_finished_still_with_no_thumbnail_still_falls_back_to_the_source(self, model):
        asked = []
        model.loader.request_image = asked.append
        self._decoration(model, {"id": "b", "name": "plate.tif", "path": "Z:/stock/plate.tif",
                                 "thumb_path": None, "status": "ready"})
        assert asked == ["Z:/stock/plate.tif"]

    def test_a_thumbnail_is_always_asked_for(self, model):
        asked = []
        model.loader.request_image = asked.append
        self._decoration(model, {"id": "c", "name": "clip.mov", "path": "Z:/stock/clip.mov",
                                 "thumb_path": "Z:/cache/ab/clip_thumb.jpg", "status": "ingesting"})
        assert asked == ["Z:/cache/ab/clip_thumb.jpg"]


class TestTheIngestStopsBeforeTheMachineDoes:
    """
    A day-long real ingest reached 100 GB and froze the workstation. The
    cause is still being looked for; until it is found, the ingest must not
    be the thing that takes the machine down.
    """

    @pytest.fixture
    def worker(self, monkeypatch, tmp_path):
        from slate.core.domain import asset_ingestor

        class QuietLibrary:
            def add_assets_batch(self, assets):
                pass

        monkeypatch.setattr(asset_ingestor, "create_asset_api", lambda **kw: QuietLibrary())
        return asset_ingestor.IngestWorker(root_path=tmp_path)

    def test_the_limit_is_far_above_normal_use(self):
        from slate.core.domain.asset_ingestor import memory_limit_mb
        assert memory_limit_mb() >= 6144

    def test_under_the_limit_it_carries_on(self, worker, monkeypatch):
        from slate.core.domain import asset_ingestor
        monkeypatch.setattr(asset_ingestor, "current_memory_mb", lambda: 900.0)
        monkeypatch.setattr(asset_ingestor, "memory_limit_mb", lambda: 6144.0)
        assert worker._memory_guard(100, 17000) is False
        assert worker.is_paused is False

    def test_over_the_limit_it_pauses_and_says_so(self, worker, monkeypatch):
        from slate.core.domain import asset_ingestor
        monkeypatch.setattr(asset_ingestor, "current_memory_mb", lambda: 40000.0)
        monkeypatch.setattr(asset_ingestor, "memory_limit_mb", lambda: 6144.0)
        heard = []
        worker.memory_alarm.connect(heard.append)
        shown = []
        worker.progress_signal.connect(lambda pct, text: shown.append(text))

        assert worker._memory_guard(9000, 17000) is True

        assert worker.is_paused is True
        assert heard and "restart Slate" in heard[0]
        assert shown and "GB" in shown[0]

    def test_it_is_raised_once(self, worker, monkeypatch):
        from slate.core.domain import asset_ingestor
        monkeypatch.setattr(asset_ingestor, "current_memory_mb", lambda: 40000.0)
        monkeypatch.setattr(asset_ingestor, "memory_limit_mb", lambda: 6144.0)
        assert worker._memory_guard(25, 100) is True
        worker.resume()                     # the person chose to go on
        assert worker._memory_guard(50, 100) is False
        assert worker.is_paused is False

    def test_the_memory_line_names_the_suspects(self):
        from slate.core.domain.asset_ingestor import describe_memory
        line = describe_memory()
        for word in ("threads", "children", "handles", "QImage", "QPixmap"):
            assert word in line, line

    def test_the_interface_hears_the_alarm(self):
        source = (ROOT / "slate" / "gui" / "tabs" / "stock_browser" / "controllers"
                  / "ingest_controller.py").read_text(encoding="utf-8")
        assert "memory_alarm.connect" in source
