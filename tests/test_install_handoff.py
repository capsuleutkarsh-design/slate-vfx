"""
The path somebody types into the installer has to be the path that gets used.

Setup asks where the studio's shared folder is and writes the answer beside the
program. The application reads that file below the per-machine settings, because
a correction made in Settings has to outrank an old install. Both rules are
right; together they mean that on any machine that has had Slate before, the
answer typed into the installer is read, ranked last and ignored. Every studio
has its own drive, so every studio hits this on the second install.

The fix is not to reorder the layers - that would make a reinstall silently
undo a Settings correction. It is to notice that an install is newer than the
settings it installs over, and fold its answers in once.
"""

import json
import time

import pytest

from slate.core.infra import install_handoff


@pytest.fixture
def machine(tmp_path, monkeypatch):
    """A machine with its own settings file and an installed program folder."""
    settings = tmp_path / "machine" / "config.json"
    settings.parent.mkdir(parents=True)
    program = tmp_path / "program"
    program.mkdir()

    monkeypatch.setattr(install_handoff, "install_answer_paths",
                        lambda: [program / "client_config.json"])

    import slate.core.infra.local_secrets as local_secrets
    monkeypatch.setattr(local_secrets, "local_config_path", lambda: settings)
    monkeypatch.setattr(local_secrets, "_candidates", lambda: iter([settings]))

    return {"settings": settings, "program": program}


def write(path, data, when=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")
    if when is not None:
        import os
        os.utime(path, (when, when))
    return path


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


# --------------------------------------------------------------- the handoff

def test_a_fresh_install_answer_is_taken(machine):
    """The whole point: what somebody typed is what the software uses."""
    write(machine["settings"], {"SERVER_ROOT": "D:/Old", "THEME": "Dark"},
          when=time.time() - 600)
    write(machine["program"] / "client_config.json",
          {"SERVER_ROOT": r"\\newserver\studio\Slate_Central"})

    changed = install_handoff.apply_install_answers()

    assert changed == {"SERVER_ROOT": r"\\newserver\studio\Slate_Central"}
    after = read(machine["settings"])
    assert after["SERVER_ROOT"] == r"\\newserver\studio\Slate_Central"
    assert after["THEME"] == "Dark", "nothing else may be disturbed"


def test_a_machine_with_no_settings_yet_takes_the_answer(machine):
    write(machine["program"] / "client_config.json", {"SERVER_ROOT": "E:/Studio"})

    assert install_handoff.apply_install_answers() == {"SERVER_ROOT": "E:/Studio"}
    assert read(machine["settings"])["SERVER_ROOT"] == "E:/Studio"


def test_an_older_install_does_not_undo_a_correction(machine):
    """
    Somebody fixed the path in Settings after installing. Reading the install
    answer again would quietly put the wrong one back, which is the failure the
    layering was built to prevent in the first place.
    """
    write(machine["program"] / "client_config.json", {"SERVER_ROOT": "D:/Typo"},
          when=time.time() - 600)
    write(machine["settings"], {"SERVER_ROOT": "D:/Correct"})

    assert install_handoff.apply_install_answers() == {}
    assert read(machine["settings"])["SERVER_ROOT"] == "D:/Correct"


def test_it_happens_once(machine):
    write(machine["settings"], {"SERVER_ROOT": "D:/Old"}, when=time.time() - 600)
    write(machine["program"] / "client_config.json", {"SERVER_ROOT": "E:/New"})

    assert install_handoff.apply_install_answers() == {"SERVER_ROOT": "E:/New"}
    assert install_handoff.apply_install_answers() == {}, \
        "the settings are now newer, so there is nothing left to take"


def test_an_answer_that_says_the_same_thing_changes_nothing(machine):
    write(machine["settings"], {"SERVER_ROOT": "E:/Studio"}, when=time.time() - 600)
    write(machine["program"] / "client_config.json", {"SERVER_ROOT": "E:/Studio"})

    assert install_handoff.apply_install_answers() == {}


def test_a_blank_answer_is_not_an_answer(machine):
    write(machine["settings"], {"SERVER_ROOT": "E:/Studio"}, when=time.time() - 600)
    write(machine["program"] / "client_config.json", {"SERVER_ROOT": ""})

    assert install_handoff.apply_install_answers() == {}
    assert read(machine["settings"])["SERVER_ROOT"] == "E:/Studio"


def test_only_what_the_installer_asks_about_is_carried_across(machine):
    """
    The file beside the program can hold anything anybody has ever put there.
    Only the questions Setup actually asks are treated as answers.
    """
    write(machine["settings"], {"SERVER_ROOT": "D:/Old", "db_password": "real"},
          when=time.time() - 600)
    write(machine["program"] / "client_config.json",
          {"SERVER_ROOT": "E:/New", "db_password": "stale", "db_port": 1234})

    install_handoff.apply_install_answers()

    after = read(machine["settings"])
    assert after["SERVER_ROOT"] == "E:/New"
    assert after["db_password"] == "real", "not a question Setup asks"
    assert "db_port" not in after


def test_a_broken_answer_file_is_ignored(machine):
    write(machine["settings"], {"SERVER_ROOT": "D:/Old"}, when=time.time() - 600)
    (machine["program"] / "client_config.json").write_text("not json",
                                                           encoding="utf-8")

    assert install_handoff.apply_install_answers() == {}
    assert read(machine["settings"])["SERVER_ROOT"] == "D:/Old"


def test_nothing_at_all_is_not_an_error(machine):
    assert install_handoff.apply_install_answers() == {}


def test_a_failure_never_stops_the_application(machine, monkeypatch):
    """
    This runs before anything else can. A studio losing its software over a
    settings file is a worse outcome than a path somebody has to retype.
    """
    def explode():
        raise RuntimeError("the disk is on fire")

    monkeypatch.setattr(install_handoff, "pending_answers", explode)
    assert install_handoff.apply_install_answers() == {}


def test_the_server_takes_the_install_answers_too():
    """
    The clients pick these up through GlobalConfig. The server never constructs
    one, so on a machine running only the server the path typed into the
    installer went nowhere - in the one place where that answer decides where
    the database gets built.
    """
    from pathlib import Path

    source = (Path(__file__).resolve().parent.parent / "slate_server"
              / "main.py").read_text(encoding="utf-8")
    assert "apply_install_answers" in source
