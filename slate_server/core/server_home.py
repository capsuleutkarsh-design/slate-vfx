"""
Where the server keeps its settings, and where its database is - without Qt.

The server window worked these out for itself. The recovery tool has to work
them out too, on a machine where the window will not start, so the answers
live here and the window asks here (slate_server/gui/app_window.py keeps its
old names for them).
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

SETTINGS_NAME = "slate_server_config.json"
# Written at every successful start: the last data directory and ports that
# worked. The recovery tool reads it when the settings file is gone or broken.
LAST_GOOD_NAME = "slate_server_last_good.json"


def server_home() -> str:
    """Running from a checkout: the checkout. Installed: %LOCALAPPDATA%\\Slate_Central."""
    if getattr(sys, "frozen", False):
        return os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")),
                            "Slate_Central")
    return str(Path(__file__).resolve().parents[2])


def default_data_dir(appdata_dir) -> str:
    """SLATE_DB_PATH when set, otherwise <server home>\\LocalDatabase. Nothing else."""
    from_env = os.environ.get("SLATE_DB_PATH")
    if from_env:
        return from_env
    return os.path.join(appdata_dir, "LocalDatabase")


def settings_path(appdata_dir) -> str:
    """
    slate_server_config.json in the server home, carrying forward an older
    install's file (copied, not moved) so an upgrade does not look like a first
    run.
    """
    current = os.path.join(appdata_dir, SETTINGS_NAME)
    if os.path.exists(current):
        return current

    local = os.path.dirname(appdata_dir)
    for folder, name in (("UT_Central", "ut_server_config.json"),):
        previous = os.path.join(local, folder, name)
        if os.path.exists(previous):
            try:
                shutil.copy2(previous, current)
                print(f"Carried settings forward from {previous}")
            except OSError as exc:
                print(f"Could not carry settings forward from {previous}: {exc}")
            return current
    return current


def read_settings(path) -> dict:
    """The settings file as a dict. Raises ValueError when it exists but is not JSON."""
    target = Path(path)
    if not target.exists():
        return {}
    data = json.loads(target.read_text(encoding="utf-8-sig"))
    if not isinstance(data, dict):
        raise ValueError("%s does not hold a settings object" % target)
    return data


def remember_last_good(appdata_dir, data_dir, port, pooler_port) -> None:
    """Write down what just worked, for a recovery on a day when nothing does."""
    try:
        target = Path(appdata_dir) / LAST_GOOD_NAME
        target.write_text(json.dumps({"db_path": str(data_dir), "port": int(port),
                                      "pooler_port": int(pooler_port)}, indent=2),
                          encoding="utf-8")
    except Exception as exc:
        logger.debug("Could not record the last good settings: %s", exc)


def last_good(appdata_dir) -> dict:
    try:
        data = json.loads((Path(appdata_dir) / LAST_GOOD_NAME).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}
