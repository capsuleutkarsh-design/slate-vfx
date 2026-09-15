"""
Make the answers somebody gives the installer actually take effect.

Setup asks where the studio's shared folder is and writes the answer beside the
program, as client_config.json. The application reads that file - but it reads
it *below* the per-machine settings, because those are what the Settings screen
writes and a correction made in Settings has to outrank an old install.

Both of those rules are right and together they produce a third thing that is
not: on any machine that has had Slate before, the path typed into the
installer is read, ranked last, and ignored. The installer appears to accept it
and nothing uses it. Every studio has its own drive, so this is the first thing
every studio hits.

The answer is not to reorder the layers. It is to notice that an install is
newer than the settings it is installing over, and to fold its answers into
them once - after which the ordinary rules apply again and the Settings screen
wins, because anything typed there is newer still.

Only the settings the installer actually asks about are carried across. It has
no opinion about the rest and must not overwrite them.
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

logger = logging.getLogger(__name__)


# What the installers write. Anything else in that file is somebody's leftover
# and is not treated as an answer to a question that was asked.
INSTALL_ANSWER_KEYS = ("SERVER_ROOT",)


def install_answer_paths() -> list:
    """Where an installer would have left its answers, nearest first."""
    paths = []

    if getattr(sys, "frozen", False):
        # Beside the executable: the program folder the installer wrote into.
        paths.append(Path(sys.executable).resolve().parent / "client_config.json")

    # A source checkout, where setup.bat plays the same part.
    root = Path(__file__).resolve().parent.parent.parent.parent
    paths.append(root / "client_config.json")

    return paths


def _read(path: Path) -> dict:
    try:
        if not path.is_file():
            return {}
        loaded = json.loads(path.read_text(encoding="utf-8"))
        return loaded if isinstance(loaded, dict) else {}
    except (OSError, ValueError) as exc:
        logger.debug("Could not read the install answers at %s: %s", path, exc)
        return {}


def pending_answers() -> tuple:
    """
    The install answers that have not been folded in yet, and where from.

    Returns (values, source). Empty when there is nothing to do - which is the
    normal case, because this only fires once after an install.
    """
    from slate.core.infra.local_secrets import local_config, local_config_path

    settings_path = local_config_path()
    try:
        settings_stamp = settings_path.stat().st_mtime if settings_path.is_file() else 0.0
    except OSError:
        settings_stamp = 0.0

    current = local_config()

    for source in install_answer_paths():
        answers = _read(source)
        if not answers:
            continue

        try:
            stamp = source.stat().st_mtime
        except OSError:
            continue

        # Older than the settings means it has already been folded in, or the
        # person has changed their mind in Settings since. Either way it has
        # had its turn.
        if stamp <= settings_stamp:
            continue

        wanted = {}
        for key in INSTALL_ANSWER_KEYS:
            value = answers.get(key)
            if value in (None, ""):
                continue
            if str(current.get(key) or "") == str(value):
                continue        # already says this
            wanted[key] = value

        if wanted:
            return wanted, source

    return {}, None


def apply_install_answers() -> dict:
    """
    Fold a fresh install's answers into the per-machine settings.

    Returns what changed, empty when nothing did. Never raises: this runs
    before anything else can, and a studio losing its application over a
    settings file is a worse outcome than a path it has to retype.
    """
    try:
        wanted, source = pending_answers()
        if not wanted:
            return {}

        from slate.core.infra.local_secrets import write_local_config
        written = write_local_config(wanted)
        logger.info("Took %s from the install at %s into %s",
                    ", ".join(sorted(wanted)), source, written)
        return wanted
    except Exception as exc:
        logger.debug("Could not apply the install answers: %s", exc)
        return {}
