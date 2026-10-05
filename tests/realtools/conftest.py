"""
Real-tools checks: the real ffmpeg, ffprobe, rvio, PostgreSQL, PgBouncer and file
libraries, on media and throwaway clusters made here. A test whose tool is not on
this machine is skipped. The throwaway cluster is the recovery lab's
(tests/test_recovery_lab.py), made once per module, on 127.0.0.1 and a free port -
never the studio's 5440/6432. Nothing here broadcasts on the LAN.
"""

import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
FFMPEG = ROOT / "slate" / "bin" / "ffmpeg.exe"
FFPROBE = ROOT / "slate" / "bin" / "ffprobe.exe"
OPENRV = ROOT / "OpenRV"
RVIO = OPENRV / "bin" / "rvio.exe"
RV_PYTHON = OPENRV / "bin" / "python3.exe"

needs_ffmpeg = pytest.mark.skipif(not (FFMPEG.exists() and FFPROBE.exists()),
                                  reason="ffmpeg/ffprobe not in slate/bin")
needs_rvio = pytest.mark.skipif(not RVIO.exists(), reason="OpenRV rvio not bundled here")


def ffmpeg(*args):
    subprocess.run([str(FFMPEG), "-v", "error", "-y", *map(str, args)],
                   check=True, timeout=60)


def make_movie(path: Path, frames=24, rate="24", size="320x240"):
    path.parent.mkdir(parents=True, exist_ok=True)
    ffmpeg("-f", "lavfi", "-i", f"testsrc=size={size}:rate={rate}", "-frames:v", frames,
           "-c:v", "libx264", "-pix_fmt", "yuv420p", path)
    return path


def make_sequence(folder: Path, name="SH010_plate", ext="exr", first=1001, count=24,
                  size="320x240"):
    """Numbered frames name.1001.ext ...; returns the printf pattern."""
    folder.mkdir(parents=True, exist_ok=True)
    pattern = folder / f"{name}.%04d.{ext}"
    extra = ["-pix_fmt", "gbrpf32le"] if ext == "exr" else []
    ffmpeg("-f", "lavfi", "-i", f"testsrc=size={size}:rate=24", "-frames:v", count,
           "-start_number", first, *extra, pattern)
    return pattern


def probe(path) -> dict:
    """ffprobe's own view of a file: frames, size, rate."""
    import json
    out = subprocess.run([str(FFPROBE), "-v", "error", "-count_frames", "-show_streams",
                          "-select_streams", "v:0", "-of", "json", str(path)],
                         capture_output=True, text=True, timeout=60, check=True).stdout
    return json.loads(out)["streams"][0]


def rvio(*args, timeout=120):
    """Run rvio headless; returns (returncode, combined output)."""
    run = subprocess.run([str(RVIO), *map(str, args)], capture_output=True, text=True,
                         encoding="utf-8", errors="replace", timeout=timeout)
    return run.returncode, run.stdout + run.stderr


@pytest.fixture
def media_dir(tmp_path):
    return tmp_path


@pytest.fixture
def isolated_proxy_manager(tmp_path):
    """The real ProxyManager with its cache in this test's folder."""
    from slate.core.domain.proxy_manager import ProxyManager
    manager = ProxyManager()
    manager.ffmpeg_path = str(FFMPEG)
    manager.cache_dir = tmp_path / "cache"
    manager.cache_dir.mkdir()
    return manager


__all__ = ["FFMPEG", "FFPROBE", "RVIO", "RV_PYTHON", "OPENRV", "needs_ffmpeg", "needs_rvio",
           "make_movie", "make_sequence", "probe", "rvio"]

from tests.test_recovery_lab import APP_PASSWORD, BIN, DBNAME, Lab  # noqa: F401
from slate.core.infra import network_discovery

# The real one, for the loopback discovery test; everything else gets the stub.
REAL_DISCOVER = network_discovery.discover_server_details


@pytest.fixture(autouse=True)
def _no_lan_broadcast(monkeypatch):
    """Building GlobalConfig with no db_host broadcasts on the LAN. Not from here."""
    monkeypatch.setattr(network_discovery, "discover_server_details",
                        lambda timeout=2.0: None)


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    """A bootstrapped, hardened throwaway Slate server (database + accounts)."""
    if not (BIN / "initdb.exe").exists():
        pytest.skip("the bundled PostgreSQL is not here")
    from slate_server.core import db_credentials

    mp = pytest.MonkeyPatch()
    for name in ("SLATE_DB_PASSWORD", "SLATE_DB_ADMIN_PASSWORD", "SLATE_DB_PATH", "PGPASSWORD"):
        mp.delenv(name, raising=False)
    lab = Lab(tmp_path_factory.mktemp("rtserver"))
    mp.setattr(db_credentials, "_config_layers", lambda: [lab.credentials])
    db_credentials.reload()
    try:
        lab.initdb()
        lab.pg_start()
        lab.bootstrap()
        yield lab
    finally:
        try:
            from slate_server.core.pgbouncer_engine import PgBouncerEngine
            PgBouncerEngine(str(lab.data), db_port=lab.port, listen_port=lab.pooler_port).stop()
        except Exception:
            pass
        lab.pg_stop()
        mp.undo()
        db_credentials._secrets_path = None             # its protected store goes with it
        db_credentials._cache = None


@pytest.fixture
def share(tmp_path, monkeypatch):
    """
    SERVER_ROOT on a temp folder. The home folder (the local fallback for a
    missing share) is moved into the sandbox too.
    """
    from slate.core.infra.global_config import GlobalConfig

    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    if GlobalConfig._instance is None:
        GlobalConfig._instance = GlobalConfig()
    root = tmp_path / "share"
    root.mkdir()
    monkeypatch.setitem(GlobalConfig._instance.data, "SERVER_ROOT", str(root))
    return root
