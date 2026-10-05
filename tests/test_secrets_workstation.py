"""
The workstation's database password: Credential Manager, never config.json.

Moving a password out of config.json is the step that could strand a PC, so
the rule is proved both ways: it leaves the file only after Credential Manager
reads it back, and a PC whose Credential Manager does not work keeps the file
exactly as it was. (Credential Manager here is the in-memory one conftest
installs - never the real one.)
"""

import json
import subprocess
from pathlib import Path

import keyring
import pytest

from slate.core.infra import local_secrets
from slate.core.infra.global_config import GlobalConfig

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def files(tmp_path, monkeypatch):
    """Two settings files this PC reads, the higher one holding the password."""
    high, low = tmp_path / "slate_config.json", tmp_path / "localappdata_config.json"
    high.write_text(json.dumps({"db_host": "10.0.0.5", "db_password": "studio pass"}),
                    encoding="utf-8")
    low.write_text(json.dumps({"db_password": "stale"}), encoding="utf-8")
    monkeypatch.setattr(local_secrets, "_password_files", lambda: iter([high, low]))
    monkeypatch.setattr(local_secrets, "_candidates", lambda: iter([high]))
    return high, low


def test_a_working_password_moves_from_config_json_to_credential_manager(files):
    high, low = files
    local_secrets.after_connect(None, "studio pass", "the settings (config.json): ...")
    assert keyring.get_password("Slate", "db_password") == "studio pass"
    assert json.loads(high.read_text()) == {"db_host": "10.0.0.5"}, "only the password went"
    assert "db_password" not in json.loads(low.read_text())


def test_nothing_leaves_the_file_unless_credential_manager_reads_it_back(files, monkeypatch):
    high, low = files
    read = lambda: (json.loads(high.read_text()), json.loads(low.read_text()))  # noqa: E731
    before = read()
    monkeypatch.setattr(keyring, "get_password", lambda service, name: None)   # does not keep it
    assert local_secrets.save_db_password("studio pass") is False
    assert read() == before
    local_secrets.after_connect(None, "studio pass", "the settings (config.json): ...")
    assert read() == before


def test_a_password_from_the_environment_or_the_old_default_is_not_kept(files):
    for source in ("the DB_PASSWORD environment variable", local_secrets.LEGACY_SOURCE):
        local_secrets.after_connect(None, "x" + source, source)
    assert keyring.get_password("Slate", "db_password") is None


def test_reconfigure_saves_to_credential_manager_not_the_file(files, monkeypatch, tmp_path):
    high, _ = files
    GlobalConfig.set("db_password", "typed in reconfigure")
    assert keyring.get_password("Slate", "db_password") == "typed in reconfigure"
    assert "db_password" not in json.loads(high.read_text())
    assert local_secrets.find_db_password() == "typed in reconfigure"

    # And no later save of the other settings writes it back out.
    config = GlobalConfig._instance
    monkeypatch.setattr(config, "config_path", tmp_path / "saved.json")
    GlobalConfig._real_save(config)
    assert "db_password" not in json.loads((tmp_path / "saved.json").read_text())


def test_after_a_refusal_the_studios_next_password_is_tried_first(files):
    keyring.set_password("Slate", "db_password", "current")
    keyring.set_password("Slate", "db_password_next", "next")
    assert local_secrets.passwords_to_try(["stale"]) == ["next", "current"]
    assert local_secrets.passwords_to_try(["next", "current"]) == []
    # Once it works it is kept as the password, and "next" is spent.
    local_secrets.save_db_password("next")
    assert keyring.get_password("Slate", "db_password") == "next"
    assert keyring.get_password("Slate", "db_password_next") is None


def test_no_shipped_file_carries_a_database_password():
    default = json.loads((ROOT / "slate" / "default_config.json").read_text(encoding="utf-8"))
    assert "db_password" not in default and "password" not in default
    tracked = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True,
                             text=True).stdout.split()
    allowed = {"slate/core/infra/local_secrets.py"}         # LEGACY_PASSWORD, migration only
    found = [name for name in tracked if name not in allowed
             and not name.startswith("tests/") and Path(ROOT, name).is_file()
             and local_secrets.LEGACY_PASSWORD.encode() in Path(ROOT, name).read_bytes()]
    assert found == []
