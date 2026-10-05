"""
Real-tools checks: the real ffmpeg, ffprobe, rvio and file libraries, on media
made here with ffmpeg. A test whose tool is not on this machine is skipped.
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
