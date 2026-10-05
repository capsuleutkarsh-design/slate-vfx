"""
Settings files with real files, and UDP discovery on the loopback interface.

    config.json               the client's (and the server's database) settings
    slate_server_config.json  the server's own settings
    UDP 54320                 NetworkBroadcaster  ->  discover_server_details
"""

import json
import shutil
import socket
import subprocess
import threading
import time
from types import SimpleNamespace

import pytest

from conftest import REAL_DISCOVER

pytestmark = pytest.mark.realtools

PASSWORD = "Pässwörd-1"


def _written_by_setup_bat(target):
    """The exact line setup\\install.ps1 uses, run by the real Windows PowerShell."""
    powershell = shutil.which("powershell.exe")
    if not powershell:
        pytest.skip("Windows PowerShell is not here")
    script = ("$config = [ordered]@{ 'db_mode' = 'postgres'; 'db_host' = '10.0.0.5'; "
              "'db_port' = 5440; 'db_name' = 'ut_vfx'; 'db_user' = 'ut_vfx_app'; "
              "'db_password' = $env:RT_PASSWORD }; "
              "$config | ConvertTo-Json -Depth 4 | Set-Content $env:RT_TARGET -Encoding UTF8")
    import os
    env = dict(os.environ, RT_PASSWORD=PASSWORD, RT_TARGET=str(target))
    target.parent.mkdir(parents=True, exist_ok=True)
    done = subprocess.run([powershell, "-NoProfile", "-NonInteractive", "-Command", script],
                          capture_output=True, text=True, env=env, timeout=60)
    assert done.returncode == 0, done.stderr
    return target


def test_a_config_json_written_by_setup_bat_is_read_by_every_reader(tmp_path, monkeypatch):
    from slate.core.infra import local_secrets
    from slate.core.infra.global_config import GlobalConfig
    from slate_server.core import db_credentials
    from slate_server.core.recovery import health  # noqa: F401  (reads the same file)

    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "appdata"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))
    monkeypatch.delenv("SLATE_DB_PASSWORD", raising=False)
    monkeypatch.delenv("SLATE_DB_ADMIN_PASSWORD", raising=False)
    config = _written_by_setup_bat(tmp_path / "appdata" / "Slate" / "config.json")
    assert config.read_bytes()[:3] == b"\xef\xbb\xbf", "PowerShell 5 writes a BOM"

    # The workstation's maintenance scripts and backups.
    monkeypatch.setattr(local_secrets, "_candidates", lambda: iter([config]))
    assert local_secrets.db_password() == PASSWORD
    assert local_secrets.db_settings()["host"] == "10.0.0.5"

    # The server's own tools.
    monkeypatch.setattr(db_credentials, "_config_layers", lambda: [config])
    db_credentials.reload()
    try:
        assert db_credentials.app_password() == PASSWORD
    finally:
        db_credentials._cache = None

    # The application itself (machine config under LOCALAPPDATA).
    fresh = GlobalConfig()
    assert fresh.data.get("db_password") == PASSWORD
    assert fresh.data.get("db_host") == "10.0.0.5"


def test_client_config_round_trip_and_a_damaged_file(tmp_path, monkeypatch):
    from slate.core.infra import local_secrets
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"db_host": "10.0.0.5", "db_password": "p"}), encoding="utf-8")
    monkeypatch.setattr(local_secrets, "_candidates", lambda: iter([config]))

    assert local_secrets.write_local_config({"db_port": 5544, "db_name": None}) == config
    assert json.loads(config.read_text(encoding="utf-8")) == \
        {"db_host": "10.0.0.5", "db_password": "p", "db_port": 5544}

    config.write_text('{"db_host": "10.0.0.5", "db_pass', encoding="utf-8")
    assert local_secrets.local_config() == {}, "a damaged file is ignored, not half-read"
    local_secrets.write_local_config({"db_port": 5545})
    assert json.loads(config.read_text(encoding="utf-8")) == {"db_port": 5545}


def test_server_settings_round_trip_bom_and_damage(tmp_path):
    from slate_server.core import server_home
    from slate_server.gui.app_window import UTServerWindow as MainWindow

    home = tmp_path / "Slate_Central"
    home.mkdir()
    old = tmp_path / "UT_Central"
    old.mkdir()
    (old / "ut_server_config.json").write_text(json.dumps({"port": 5441}), encoding="utf-8")
    path = server_home.settings_path(str(home))
    assert json.loads(open(path, encoding="utf-8").read()) == {"port": 5441}, \
        "an older install's settings are carried forward"

    # Edited in Notepad: a BOM and a non-ASCII folder.
    with open(path, "w", encoding="utf-8-sig") as f:
        json.dump({"db_path": "D:\\Données\\LocalDatabase", "port": 5441}, f,
                  ensure_ascii=False)
    assert server_home.read_settings(path)["db_path"] == "D:\\Données\\LocalDatabase"
    window = SimpleNamespace(config_path=path)
    assert MainWindow._server_config(window)["db_path"] == "D:\\Données\\LocalDatabase", \
        "the window and the recovery tool must read the same folder"

    with open(path, "w", encoding="utf-8") as f:
        f.write('{"db_path": "D:\\\\Studio", "po')
    with pytest.raises(ValueError):
        server_home.read_settings(path)
    assert MainWindow._server_config(window) is None, "a damaged file is never overwritten"

    server_home.remember_last_good(str(home), "D:\\Studio\\LocalDatabase", 5441, 6433)
    assert server_home.last_good(str(home)) == {"db_path": "D:\\Studio\\LocalDatabase",
                                                "port": 5441, "pooler_port": 6433}


# =============================================================== discovery

def _free_udp_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


@pytest.fixture
def loopback_only(monkeypatch):
    """
    Keep the real sockets on 127.0.0.1: the broadcaster binds to the loopback
    instead of every interface, and the client's 255.255.255.255:54320 goes to
    the broadcaster's free port. Nothing leaves this PC, and the real 54320
    (a Slate Server may be running here) is never touched.
    """
    port = _free_udp_port()
    real = socket.socket

    class Loopback(real):
        def bind(self, address):
            host, number = address
            return super().bind(("127.0.0.1" if host in ("", "0.0.0.0") else host, number))

        def sendto(self, data, address):
            if address == ("255.255.255.255", 54320):
                address = ("127.0.0.1", port)
            assert address[0] == "127.0.0.1", "nothing may leave this PC"
            return super().sendto(data, address)
    monkeypatch.setattr(socket, "socket", Loopback)
    return port


def test_a_workstation_finds_an_announcing_server_and_its_ports(loopback_only):
    from slate_server.core.network_broadcaster import NetworkBroadcaster
    server = NetworkBroadcaster(db_port=5544, listen_port=loopback_only, pooler_port=6544)
    server.start()
    try:
        for _ in range(50):
            if server.sock is not None:
                break
            time.sleep(0.05)
        time.sleep(0.2)
        found = REAL_DISCOVER(timeout=2.0)
    finally:
        server.stop()
    assert found == {"host": "127.0.0.1", "db_port": 5544, "pooler_port": 6544}
    assert not server.is_alive(), "the broadcaster stops when asked"


def test_no_server_answering_is_a_quick_none(loopback_only):
    started = time.monotonic()
    assert REAL_DISCOVER(timeout=1.0) is None
    assert time.monotonic() - started < 3


def test_a_workstation_follows_the_ports_a_known_server_announces(share, monkeypatch):
    """The answer is adopted: same server, new database and pool ports."""
    from slate.core.infra.global_config import GlobalConfig
    from slate.core.infra.postgres_manager import PostgresManager
    data = GlobalConfig._instance.data
    for key in ("db_host", "db_port", "db_pooler_port"):
        monkeypatch.setitem(data, key, data.get(key))
    class Manager(SimpleNamespace):
        _pooler_failed_at = {}
    manager = Manager(host_candidates=["127.0.0.1"], saved_host="127.0.0.1",
                      port=5440, pooler_port=6432)
    found = {"host": "127.0.0.1", "db_port": 5544, "pooler_port": 6544}
    assert PostgresManager.adopt_announcement(manager, found) is True
    assert (manager.port, manager.pooler_port) == (5544, 6544)
    assert GlobalConfig.get("db_port") == 5544 and GlobalConfig.get("db_pooler_port") == 6544

    # Any other PC answering is never adopted over the saved server.
    stranger = {"host": "10.9.9.9", "db_port": 1, "pooler_port": 2}
    assert PostgresManager.adopt_announcement(manager, stranger) is False
    assert manager.host_candidates == ["127.0.0.1"] and manager.port == 5544
