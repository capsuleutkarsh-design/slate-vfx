"""
No studio's drive letter belongs in the source.

Several places needed a root to look under and, having none, used a literal:
Path("X:/"), "X:/Projects", r"X:\\Extra\\Slate_Central\\Database". That is one
customer's answer compiled into every copy. On every other studio it is a path
that cannot exist, so the guess never matches and the failure reads as missing
data rather than a missing setting - and in the server's case it silently built
an empty database somewhere nobody was told about.

The rule these tests hold: a path a studio has to be able to change is a
setting, and when it is not set the answer is None rather than somebody else's
drive.
"""

import re
import tokenize
from pathlib import Path

import pytest

from slate.core.infra import studio_paths


ROOT = Path(__file__).resolve().parent.parent

# A string that begins with a drive letter. URLs (http://) do not match because
# the scheme is longer than one character.
DRIVE_LITERAL = re.compile(r"^[A-Za-z]:[\\/]")


@pytest.fixture(autouse=True)
def no_inherited_environment(monkeypatch):
    for name in studio_paths.PROJECTS_ROOT_VARS + studio_paths.STUDIO_ROOT_VARS:
        monkeypatch.delenv(name, raising=False)


def _no_settings(monkeypatch):
    monkeypatch.setattr(studio_paths, "_global_setting", lambda key: "")
    monkeypatch.setattr(studio_paths, "_machine_setting", lambda key: "")


# ------------------------------------------------------------- the answers

def test_an_unconfigured_studio_gets_no_projects_root(monkeypatch):
    """
    None is a real answer. The alternative - a default drive letter - is a
    guess that is wrong everywhere except the one studio it was written for.
    """
    _no_settings(monkeypatch)
    assert studio_paths.projects_root() is None
    assert studio_paths.project_folder("MARVEL") is None


def test_the_environment_is_read_first(monkeypatch):
    _no_settings(monkeypatch)
    monkeypatch.setenv("SLATE_PROJECTS_ROOT", r"D:\Work")
    assert studio_paths.projects_root() == Path(r"D:\Work")
    assert studio_paths.project_folder("MARVEL") == Path(r"D:\Work\MARVEL")


def test_the_older_environment_spelling_still_works(monkeypatch):
    """It appears in studio launch scripts that nobody here can edit."""
    _no_settings(monkeypatch)
    monkeypatch.setenv("Slate_PROJECTS_ROOT", r"E:\Shows")
    assert studio_paths.projects_root() == Path(r"E:\Shows")


def test_the_machine_setting_is_used_when_there_is_no_environment(monkeypatch):
    monkeypatch.setattr(studio_paths, "_global_setting", lambda key: "")
    monkeypatch.setattr(studio_paths, "_machine_setting",
                        lambda key: r"P:\Projects" if key == "last_project_dir" else "")
    assert studio_paths.projects_root() == Path(r"P:\Projects")


def test_projects_fall_under_the_studio_folder_when_nothing_else_says(monkeypatch):
    monkeypatch.setattr(studio_paths, "_machine_setting", lambda key: "")
    monkeypatch.setattr(
        studio_paths, "_global_setting",
        lambda key: r"\\studio\share\Slate_Central" if key == "SERVER_ROOT" else "")

    assert studio_paths.projects_root() == Path(r"\\studio\share\Slate_Central\Projects")


def test_an_empty_project_code_has_no_folder(monkeypatch):
    monkeypatch.setenv("SLATE_PROJECTS_ROOT", r"D:\Work")
    _no_settings(monkeypatch)
    assert studio_paths.project_folder("") is None
    assert studio_paths.project_folder("   ") is None


def test_nothing_is_cached(monkeypatch):
    """A cached root is how a corrected path fails to take effect."""
    _no_settings(monkeypatch)
    monkeypatch.setenv("SLATE_PROJECTS_ROOT", r"D:\First")
    assert studio_paths.projects_root() == Path(r"D:\First")
    monkeypatch.setenv("SLATE_PROJECTS_ROOT", r"D:\Second")
    assert studio_paths.projects_root() == Path(r"D:\Second")


# ---------------------------------------------------- nothing is hard-coded

def _source_files():
    skip = ("__pycache__", "bin", "OpenRV", "legacy_archive", "dev_debug")
    for folder in ("slate", "slate_server", "tools"):
        for path in (ROOT / folder).rglob("*.py"):
            if any(part in skip for part in path.parts):
                continue
            yield path


def _string_literals(path):
    """Every string in the file that is not a docstring."""
    with open(path, "rb") as handle:
        try:
            for token in tokenize.tokenize(handle.readline):
                if token.type != tokenize.STRING:
                    continue
                text = token.string
                # Triple-quoted strings are documentation here, and examples in
                # documentation are how the thing is explained.
                if text.lstrip("rbuRBUf").startswith(('"""', "'''")):
                    continue
                yield token.start[0], text
        except (tokenize.TokenError, SyntaxError, UnicodeDecodeError):
            return


# Windows puts these in the same place on every machine, so looking for a
# program under them is discovery rather than a decision taken on a studio's
# behalf. A studio never needs to change them, which is the whole test.
WINDOWS_LOCATIONS = (r"c:\program files", "c:/program files",
                     r"c:\windows", "c:/windows")


def _is_windows_location(text: str) -> bool:
    lowered = text.replace("\\\\", "\\").lower()
    if lowered in ("c:\\", "c:/"):
        return True
    return lowered.startswith(WINDOWS_LOCATIONS)


def test_no_source_file_hard_codes_a_studio_drive():
    """
    The test that would have caught every one of them. A path beginning with a
    drive letter, written into the source, is a decision taken on behalf of a
    studio that has not been asked.
    """
    offenders = []
    for path in _source_files():
        for line, text in _string_literals(path):
            inner = text.strip("rbuRBUf").strip("\"'")
            if DRIVE_LITERAL.match(inner) and not _is_windows_location(inner):
                offenders.append("%s:%d  %s" % (path.relative_to(ROOT), line, text))

    assert not offenders, (
        "These are somebody's drive letter compiled into the software:\n  "
        + "\n  ".join(offenders))


def test_the_shipped_settings_name_no_studio_folder():
    """
    default_config.json used to ship SERVER_ROOT as C:/Slate_Central, a folder
    that exists on nobody's machine. Absent means GlobalConfig's own per-user
    default applies until the studio says otherwise.
    """
    import json

    shipped = json.loads((ROOT / "slate" / "default_config.json")
                         .read_text(encoding="utf-8"))
    assert "SERVER_ROOT" not in shipped
    for key, value in shipped.items():
        if isinstance(value, str):
            assert not DRIVE_LITERAL.match(value), \
                "default_config.json ships %s = %r" % (key, value)


def test_the_installers_do_not_pre_fill_a_studio_folder():
    """
    The wizard page was pre-filled with X:\\Extra\\Slate_Central, so pressing
    Next without reading it installed a machine pointed at a drive that does
    not exist - and nothing said so until somebody wondered why nothing was
    being shared.
    """
    for name in ("setup_slate_client.iss", "setup_slate_ops.iss",
                 "setup_slate_server.iss"):
        text = (ROOT / "deployment" / name).read_text(encoding="utf-8")
        assert "ServerPathPage.Values[0] := '';" in text, \
            "%s still pre-fills the studio folder" % name
        assert "StudioFolderAccepted" in text, \
            "%s accepts a blank studio folder" % name
