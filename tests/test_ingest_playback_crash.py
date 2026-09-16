"""
The program died, without a word in the log, while a folder was being ingested
and a clip was clicked to play.

Nothing Python-level was found in the log because nothing Python-level went
wrong: the process was killed from inside a native library. Three things in
that code path could do exactly that, and a fourth kept the next occurrence
from being diagnosable at all:

  1. GlobalConfig.server_root() opened a modal QMessageBox on whichever thread
     asked for the studio folder while the share was unreachable. The update
     checker, the backup thread and the ingest all ask from worker threads,
     and a widget on a worker thread is a hard crash inside Qt.
  2. The ingest handed the gallery the very dict objects it kept writing
     into from its own thread during the deep analysis.
  3. Thumbnails and proxies were written straight to their final name, so a
     half-written file could be opened by the gallery or the player, and a
     stump left by a crash was taken for finished ever after.
  4. No faulthandler: a native crash left no stack for anyone.
"""

import os
import subprocess
import threading
from pathlib import Path

import pytest


@pytest.fixture(scope="module")
def qapp():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


# --------------------------------------------------------------------------- 1
class TestTheStudioFolderNoticeStaysOnTheInterfaceThread:

    @pytest.fixture
    def missing_root(self, monkeypatch, tmp_path):
        from slate.core.infra.global_config import GlobalConfig
        gone = tmp_path / "share-that-dropped-out"
        monkeypatch.setattr(GlobalConfig, "get",
                            classmethod(lambda cls, key, default=None: str(gone)))
        if hasattr(GlobalConfig, "_network_warning_shown"):
            monkeypatch.delattr(GlobalConfig, "_network_warning_shown")
        monkeypatch.setattr(GlobalConfig, "_notice_bridge", None)
        return gone

    def test_asked_from_a_worker_thread_it_answers_and_shows_nothing_there(self, qapp, missing_root):
        from slate.core.infra.global_config import GlobalConfig
        answer = {}

        def ask():
            answer["root"] = GlobalConfig.server_root()

        worker = threading.Thread(target=ask, name="pretend-update-checker")
        worker.start()
        worker.join(timeout=10)
        assert not worker.is_alive(), "the worker thread hung inside server_root()"

        assert answer["root"].exists(), "a local fallback folder should be handed back"
        bridge = GlobalConfig._notice_bridge
        assert bridge is not None
        assert bridge.thread() is qapp.thread(), "the notice bridge must live on the interface thread"
        assert bridge.shown == [], "nothing may be shown until the interface thread gets to it"

        qapp.processEvents()
        assert len(bridge.shown) == 1
        box = bridge.shown[0]
        assert box.thread() is qapp.thread()
        assert not box.isModal(), "the notice must not block whatever the interface was doing"

    def test_asked_from_the_interface_thread_it_still_does_not_block(self, qapp, missing_root):
        from slate.core.infra.global_config import GlobalConfig
        root = GlobalConfig.server_root()      # would have sat in exec() before
        assert root.exists()
        qapp.processEvents()
        assert len(GlobalConfig._notice_bridge.shown) == 1

    def test_it_is_said_once_per_session(self, qapp, missing_root):
        from slate.core.infra.global_config import GlobalConfig
        GlobalConfig.server_root()
        GlobalConfig.server_root()
        qapp.processEvents()
        assert len(GlobalConfig._notice_bridge.shown) == 1

    def test_no_dialog_is_built_inline_any_more(self):
        import inspect
        from slate.core.infra import global_config
        body = inspect.getsource(global_config.GlobalConfig.server_root)
        assert "QMessageBox" not in body
        assert ".exec()" not in body


# --------------------------------------------------------------------------- 2
class TestTheIngestHandsTheInterfaceItsOwnCopies:

    @pytest.fixture
    def worker(self, monkeypatch, tmp_path):
        from slate.core.domain import asset_ingestor

        class QuietLibrary:
            def add_assets_batch(self, assets):
                pass

        monkeypatch.setattr(asset_ingestor, "create_asset_api", lambda **kw: QuietLibrary())
        return asset_ingestor.IngestWorker(root_path=tmp_path)

    @staticmethod
    def _asset(name):
        return {"id": name, "name": name, "path": f"Z:/stock/{name}", "tags": ["Pending"],
                "metadata": {}, "status": "ingesting"}

    def test_the_discovery_batch_is_a_copy(self, worker):
        original = self._asset("a.mov")
        worker._buffer = [original]
        received = []
        worker.assets_batch_signal.connect(received.append)

        worker._flush_buffer()

        assert received and received[0][0] == original
        assert received[0][0] is not original
        # what the worker does next must not reach the interface's copy
        original["metadata"]["width"] = 1920
        original["tags"].append("Sequence")
        assert received[0][0]["metadata"] == {}
        assert received[0][0]["tags"] == ["Pending"]

    def test_the_analysis_batch_is_a_copy_too(self, worker):
        original = self._asset("b.mov")
        worker._update_buffer = [original]
        received = []
        worker.assets_update_batch_signal.connect(received.append)

        worker._flush_update_buffer()

        assert received[0][0] == original and received[0][0] is not original


# --------------------------------------------------------------------------- 3
class _FakeFfmpeg:
    """Stands in for subprocess.run: writes what it is told to, where it is told to."""

    def __init__(self, write=b"jpegjpegjpeg", returncode=0, raise_timeout=False):
        self.write = write
        self.returncode = returncode
        self.raise_timeout = raise_timeout
        self.outputs = []

    def __call__(self, cmd, **kw):
        out = Path(cmd[-1])
        self.outputs.append(out)
        out.parent.mkdir(parents=True, exist_ok=True)
        if self.write:
            out.write_bytes(self.write)
        if self.raise_timeout:
            raise subprocess.TimeoutExpired(cmd, kw.get("timeout", 1))

        class Result:
            returncode = self.returncode
            stderr = ""
        return Result()


class TestCacheFilesOnlyAppearWhenComplete:

    @pytest.fixture
    def manager(self, tmp_path):
        from slate.core.domain.proxy_manager import ProxyManager
        pm = ProxyManager.__new__(ProxyManager)
        pm.ffmpeg_path = "ffmpeg"
        pm.cache_dir = tmp_path / "cache"
        pm.cache_dir.mkdir()
        return pm

    @pytest.fixture
    def clip(self, tmp_path):
        src = tmp_path / "shot.mov"
        src.write_bytes(b"\x00" * 64)
        return src

    @staticmethod
    def _leftovers(folder):
        return [p for p in folder.rglob("*.part*")]

    def test_ffmpeg_is_pointed_at_a_working_name_with_the_same_extension(self, manager, clip, monkeypatch):
        from slate.core.domain import proxy_manager
        fake = _FakeFfmpeg()
        monkeypatch.setattr(proxy_manager.subprocess, "run", fake)

        ok, thumb = manager.generate_thumbnail(clip)

        assert ok and thumb.exists() and thumb.stat().st_size > 0
        given = fake.outputs[0]
        assert given != thumb, "ffmpeg must never write to the final name"
        assert given.suffix == thumb.suffix, "ffmpeg picks the format from the extension"
        assert self._leftovers(manager.cache_dir) == []

    def test_a_failed_thumbnail_leaves_nothing_under_the_final_name(self, manager, clip, monkeypatch):
        from slate.core.domain import proxy_manager
        fake = _FakeFfmpeg(write=b"half", returncode=1)
        monkeypatch.setattr(proxy_manager.subprocess, "run", fake)

        ok, thumb = manager.generate_thumbnail(clip)

        assert ok is False and thumb is None
        assert list(manager.cache_dir.rglob("*_thumb.jpg")) == []
        assert self._leftovers(manager.cache_dir) == []

    def test_a_timed_out_thumbnail_is_cleaned_up(self, manager, clip, monkeypatch):
        from slate.core.domain import proxy_manager
        monkeypatch.setattr(proxy_manager.subprocess, "run", _FakeFfmpeg(write=b"half", raise_timeout=True))

        ok, _ = manager.generate_thumbnail(clip)

        assert ok is False
        assert self._leftovers(manager.cache_dir) == []

    def test_a_stump_from_an_earlier_crash_is_not_taken_for_a_thumbnail(self, manager, clip, monkeypatch):
        from slate.core.domain import proxy_manager
        expected = manager.cache_path_for(manager.get_hash(clip), "_thumb.jpg", manager.identity_hash(clip))
        expected.write_bytes(b"")                       # what an interrupted write used to leave
        fake = _FakeFfmpeg()
        monkeypatch.setattr(proxy_manager.subprocess, "run", fake)

        ok, thumb = manager.generate_thumbnail(clip)

        assert ok and thumb == expected and thumb.stat().st_size > 0
        assert fake.outputs, "the stump should have been redone, not trusted"

    def test_a_proxy_appears_only_once_it_is_whole(self, manager, clip, tmp_path, monkeypatch):
        from slate.core.domain import proxy_manager
        target = tmp_path / "shot_proxy.mp4"
        fake = _FakeFfmpeg(write=b"mp4" * 100)
        monkeypatch.setattr(proxy_manager.subprocess, "run", fake)

        ok, proxy = manager.generate_proxy(source_path=clip, proxy_path=target)

        assert ok and proxy == target and target.exists()
        assert fake.outputs[0] != target and fake.outputs[0].suffix == ".mp4"
        assert self._leftovers(tmp_path) == []

    def test_a_proxy_that_failed_part_way_is_not_left_for_the_player(self, manager, clip, tmp_path, monkeypatch):
        from slate.core.domain import proxy_manager
        target = tmp_path / "shot_proxy.mp4"
        monkeypatch.setattr(proxy_manager.subprocess, "run", _FakeFfmpeg(write=b"moov-less", returncode=1))

        ok, proxy = manager.generate_proxy(source_path=clip, proxy_path=target)

        assert ok is False and proxy is None
        assert not target.exists(), "the player checks exists(); a stump would be played"
        assert self._leftovers(tmp_path) == []

    def test_an_empty_proxy_is_redone(self, manager, clip, tmp_path, monkeypatch):
        from slate.core.domain import proxy_manager
        target = tmp_path / "shot_proxy.mp4"
        target.write_bytes(b"")
        fake = _FakeFfmpeg(write=b"mp4" * 100)
        monkeypatch.setattr(proxy_manager.subprocess, "run", fake)

        ok, _ = manager.generate_proxy(source_path=clip, proxy_path=target)

        assert ok and target.stat().st_size > 0 and fake.outputs


# --------------------------------------------------------------------------- 4
class TestANativeCrashLeavesAStackBehind:

    def test_faulthandler_is_switched_on_into_the_log_folder(self, monkeypatch, tmp_path):
        import faulthandler
        from slate.core.infra import crash_handler
        monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))

        crash_handler._enable_native_crash_dump()

        assert faulthandler.is_enabled()
        dump = tmp_path / "Slate" / "Logs" / "native_crash.log"
        assert dump.exists()
        assert "pid" in dump.read_text()

    def test_it_is_part_of_the_normal_crash_handler_setup(self):
        import inspect
        from slate.core.infra import crash_handler
        assert "_enable_native_crash_dump()" in inspect.getsource(crash_handler.setup_global_crash_handler)
