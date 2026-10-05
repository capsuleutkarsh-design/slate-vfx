"""
One database-password lookup (B5), and nobody loses a password they had.

PostgresManager, local_secrets and the SQLAlchemy factory each looked in
different places in a different order. They now share
local_secrets.find_db_password(). These tests prove every place any of the
three used to look is still looked in, in the documented order, and that the
client's start-up order (DB_PASSWORD, settings, keyring, encrypted file) is
unchanged.
"""

import json
import sys
import types

import pytest

from slate.core.infra import local_secrets
from slate.core.infra.global_config import GlobalConfig


@pytest.fixture
def sources(tmp_path, monkeypatch):
    """Every source empty, with a handle to fill each one."""
    settings, store = {}, {}
    monkeypatch.delenv("DB_PASSWORD", raising=False)
    monkeypatch.delenv(local_secrets.ENV_VAR, raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setattr(GlobalConfig, "get",
                        classmethod(lambda cls, key, default=None: settings.get(key, default)))
    monkeypatch.setitem(sys.modules, "keyring", types.SimpleNamespace(
        get_password=lambda service, key: store.get((service, key))))
    local_file = tmp_path / "config.json"
    monkeypatch.setattr(local_secrets, "_candidates", lambda: iter([local_file]))

    def encrypted_file(password):
        from cryptography.fernet import Fernet
        key = Fernet.generate_key()
        (tmp_path / "Slate").mkdir(exist_ok=True)
        (tmp_path / "Slate" / ".encryption_key").write_bytes(key)
        (tmp_path / "Slate" / ".db_credentials").write_bytes(
            Fernet(key).encrypt(json.dumps({"db_password": password}).encode()))

    fill = {
        "DB_PASSWORD env": lambda v: monkeypatch.setenv("DB_PASSWORD", v),
        "SLATE_DB_PASSWORD env": lambda v: monkeypatch.setenv(local_secrets.ENV_VAR, v),
        "settings db_password": lambda v: settings.__setitem__("db_password", v),
        "settings password": lambda v: settings.__setitem__("password", v),
        "keyring": lambda v: store.__setitem__(("Slate", "db_password"), v),
        "encrypted file": encrypted_file,
        "db_config password": lambda v: settings.__setitem__("db_config", {"password": v}),
        "local config file": lambda v: local_file.write_text(json.dumps({"db_password": v})),
    }
    return fill


# Highest first - the documented order.
ORDER = ["DB_PASSWORD env", "settings db_password", "settings password",
         "keyring", "encrypted file", "SLATE_DB_PASSWORD env", "db_config password",
         "local config file"]


@pytest.mark.parametrize("source", ORDER)
def test_every_source_any_lookup_used_is_still_honoured(sources, source):
    sources[source]("pass word " + source)          # spaces kept, never stripped
    assert local_secrets.find_db_password() == "pass word " + source


def test_the_order_is_the_documented_one(sources):
    for source in reversed(ORDER):                  # each new, higher source takes over
        sources[source](source)
        assert local_secrets.find_db_password() == source


def test_the_client_start_up_order_is_unchanged(sources):
    """What PostgresManager always did: DB_PASSWORD > settings > keyring > encrypted file."""
    for source in reversed(["DB_PASSWORD env", "settings db_password", "keyring", "encrypted file"]):
        sources[source](source)
        assert local_secrets.find_db_password() == source


def test_a_blank_setting_does_not_hide_the_keyring(sources):
    sources["settings db_password"]("   ")
    sources["keyring"]("from keyring")
    assert local_secrets.find_db_password() == "from keyring"


def test_both_readers_use_it(sources):
    from slate.core.infra.postgres_manager import PostgresManager

    sources["keyring"]("k")
    assert local_secrets.db_password() == "k"
    assert PostgresManager._load_password_secure(None) == "k"


def test_none_found_still_says_so(sources):
    from slate.core.infra.postgres_manager import PostgresManager

    assert local_secrets.find_db_password() == ""
    with pytest.raises(RuntimeError):
        local_secrets.db_password()
    assert local_secrets.db_password(required=False) == ""
    with pytest.raises(RuntimeError):
        PostgresManager._load_password_secure(None)
