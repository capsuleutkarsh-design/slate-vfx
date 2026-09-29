"""
Opening a shot in Nuke, Blender and the rest has to find the program that is
actually installed, and open Nuke the way the studio works in it.

Two things went wrong before. The launcher only knew a handful of old version
folders (Nuke 13.2 to 15.0, Blender 3.6 and 4.0), so a machine with anything
newer was asked to browse for the program. And the shot panel's own search
sorted what it found as text, where "Nuke9.0v8" comes after "Nuke16.0v2" -
the oldest install won. Nuke also always opened as plain Nuke, when the
compositors work in NukeX.

None of this needs a real Nuke: the installs here are empty files in a temporary
Program Files.
"""

import os
from types import SimpleNamespace

import pytest

from slate.core import dcc_launcher
from slate.core.dcc_launcher import (
    DCCLauncher, app_label, build_command, find_installed, get_nuke_mode,
    newest, parse_version, resolve_executable,
)


class FakeConfig:
    """Stands in for ConfigManager: global settings in memory, saves recorded."""

    def __init__(self, **global_settings):
        self.settings = {"global_settings": dict(global_settings)}
        self.saved = []

    def update_global_settings(self, new_settings):
        self.saved.append(dict(new_settings))
        self.settings["global_settings"].update(new_settings)
        return True


def _install(root, *relative_paths):
    """Create empty stand-ins for installed programs; return their full paths."""
    made = []
    for rel in relative_paths:
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"")
        made.append(str(path))
    return made


@pytest.fixture
def program_files(tmp_path, monkeypatch):
    """An empty Program Files, and nothing on this machine leaking in."""
    root = tmp_path / "Program Files"
    root.mkdir()
    monkeypatch.setenv("PROGRAMFILES", str(root))
    monkeypatch.delenv("PROGRAMW6432", raising=False)
    empty = tmp_path / "empty_path"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))
    for info in dcc_launcher.DCC_APPS.values():
        monkeypatch.delenv(info["env_path"], raising=False)
    monkeypatch.setattr(dcc_launcher, "_legacy_setting", lambda key: "")
    return root


class TestVersionSorting:
    def test_versions_are_read_as_numbers(self):
        assert parse_version(r"C:\Program Files\Nuke15.1v3\Nuke15.1.exe") == (15, 1, 3)
        assert parse_version(r"C:\Program Files\Blender Foundation\Blender 4.2\blender.exe") == (4, 2)
        assert parse_version(
            r"C:\Program Files\Adobe\Adobe After Effects 2024\Support Files\AfterFX.exe") == (2024,)
        assert parse_version(r"C:\Program Files\INRIA\Natron-2.5.0\bin\Natron.exe") == (2, 5, 0)

    def test_nuke_16_beats_nuke_9(self):
        # As text, "Nuke9" sorts after "Nuke16" and the oldest install won.
        paths = [
            r"C:\Program Files\Nuke9.0v8\Nuke9.0.exe",
            r"C:\Program Files\Nuke16.0v2\Nuke16.0.exe",
            r"C:\Program Files\Nuke15.1v3\Nuke15.1.exe",
        ]
        assert newest(paths) == r"C:\Program Files\Nuke16.0v2\Nuke16.0.exe"

    def test_the_patch_release_counts(self):
        paths = [
            r"C:\Program Files\Nuke15.1v10\Nuke15.1.exe",
            r"C:\Program Files\Nuke15.1v3\Nuke15.1.exe",
        ]
        assert newest(paths) == r"C:\Program Files\Nuke15.1v10\Nuke15.1.exe"

    def test_blender_4_10_beats_4_2(self):
        paths = [
            r"C:\Program Files\Blender Foundation\Blender 4.2\blender.exe",
            r"C:\Program Files\Blender Foundation\Blender 4.10\blender.exe",
        ]
        assert newest(paths).endswith(r"Blender 4.10\blender.exe")

    def test_nothing_installed(self):
        assert newest([]) == ""


class TestFindingInstalls:
    def test_newest_nuke_is_found_and_helpers_are_ignored(self, program_files):
        _install(
            program_files,
            r"Nuke9.0v8\Nuke9.0.exe",
            r"Nuke15.1v3\Nuke15.1.exe",
            r"Nuke15.1v3\python.exe",
            r"Nuke16.0v2\Nuke16.0.exe",
            r"Nuke16.0v2\crashpad_handler.exe",
            r"Nuke16.0v2\NukeCrashFeedback.exe",
        )
        found = find_installed("nuke")
        assert os.path.basename(found) == "Nuke16.0.exe"
        assert "Nuke16.0v2" in found

    def test_nuke_under_a_foundry_folder(self, program_files):
        _install(program_files, r"Foundry\Nuke15.1v3\Nuke15.1.exe")
        assert find_installed("nuke").endswith(r"Foundry\Nuke15.1v3\Nuke15.1.exe")

    def test_blender_launcher_is_not_blender(self, program_files):
        _install(
            program_files,
            r"Blender Foundation\Blender 3.6\blender.exe",
            r"Blender Foundation\Blender 4.2\blender.exe",
            r"Blender Foundation\Blender 4.2\blender-launcher.exe",
        )
        assert find_installed("blender").endswith(r"Blender 4.2\blender.exe")

    def test_other_apps(self, program_files):
        _install(
            program_files,
            r"INRIA\Natron-2.5.0\bin\Natron.exe",
            r"INRIA\Natron-2.5.0\bin\NatronRenderer.exe",
            r"Adobe\Adobe After Effects 2023\Support Files\AfterFX.exe",
            r"Adobe\Adobe After Effects 2024\Support Files\AfterFX.exe",
            r"Adobe\Adobe Premiere Pro 2024\Adobe Premiere Pro.exe",
            r"BorisFX\Silhouette 2023.5\silhouette.exe",
        )
        assert find_installed("natron").endswith(r"Natron-2.5.0\bin\Natron.exe")
        assert "2024" in find_installed("after_effects")
        assert find_installed("premiere").endswith("Adobe Premiere Pro.exe")
        assert find_installed("silhouette").endswith(r"Silhouette 2023.5\silhouette.exe")

    def test_nothing_installed(self, program_files):
        assert find_installed("nuke") == ""


class TestResolveOrder:
    def test_a_saved_path_that_exists_wins(self, program_files, tmp_path):
        chosen = _install(tmp_path, r"tools\Nuke14.0v5\Nuke14.0.exe")[0]
        _install(program_files, r"Nuke16.0v2\Nuke16.0.exe")
        cfg = FakeConfig(dcc_path_nuke=chosen)
        assert resolve_executable("nuke", cfg) == chosen
        assert cfg.saved == []

    def test_a_stale_saved_path_is_replaced(self, program_files):
        # Nuke upgraded: the saved folder is gone, the new one is found and saved.
        new = _install(program_files, r"Nuke16.0v2\Nuke16.0.exe")[0]
        cfg = FakeConfig(dcc_path_nuke=r"C:\Program Files\Nuke15.1v3\Nuke15.1.exe-gone")
        assert resolve_executable("nuke", cfg) == new
        assert cfg.saved == [{"dcc_path_nuke": new}]

    def test_the_older_config_key_still_works(self, program_files, tmp_path, monkeypatch):
        legacy = _install(tmp_path, r"old\Nuke13.2v4\Nuke13.2.exe")[0]
        _install(program_files, r"Nuke16.0v2\Nuke16.0.exe")
        monkeypatch.setattr(dcc_launcher, "_legacy_setting",
                            lambda key: legacy if key == "nuke_path" else "")
        assert resolve_executable("nuke", FakeConfig()) == legacy

    def test_the_environment_variable_comes_before_searching(self, program_files, tmp_path, monkeypatch):
        from_env = _install(tmp_path, r"env\blender.exe")[0]
        _install(program_files, r"Blender Foundation\Blender 4.2\blender.exe")
        monkeypatch.setenv("Slate_BLENDER_PATH", from_env)
        assert resolve_executable("blender", FakeConfig()) == from_env

    def test_path_is_searched_last(self, program_files, tmp_path, monkeypatch):
        on_path = _install(tmp_path, r"bin\Nuke15.1.exe", r"bin\Nuke9.0.exe")
        monkeypatch.setenv("PATH", str(tmp_path / "bin"))
        assert resolve_executable("nuke", FakeConfig()) == on_path[0]

    def test_not_found_anywhere(self, program_files):
        cfg = FakeConfig()
        assert resolve_executable("nuke", cfg) == ""
        assert cfg.saved == []


class TestNukeMode:
    EXE = r"C:\Program Files\Nuke15.1v3\Nuke15.1.exe"
    SCRIPT = r"S:\show\sh010\comp\sh010_comp_v003.nk"

    def test_nukex_by_default(self):
        assert build_command("nuke", self.EXE, self.SCRIPT) == [self.EXE, "--nukex", self.SCRIPT]
        assert get_nuke_mode(FakeConfig()) == "nukex"
        assert app_label("nuke") == "NukeX"

    def test_plain_nuke(self):
        assert build_command("nuke", self.EXE, self.SCRIPT, "nuke") == [self.EXE, self.SCRIPT]
        assert app_label("nuke", "nuke") == "Nuke"

    def test_nuke_studio(self):
        assert build_command("nuke", self.EXE, self.SCRIPT, "studio") == [self.EXE, "--studio", self.SCRIPT]
        assert app_label("nuke", "studio") == "Nuke Studio"

    def test_without_a_script(self):
        assert build_command("nuke", self.EXE) == [self.EXE, "--nukex"]

    def test_an_unknown_mode_falls_back_to_nukex(self):
        assert get_nuke_mode(FakeConfig(nuke_mode="hiero")) == "nukex"
        assert build_command("nuke", self.EXE, None, "hiero") == [self.EXE, "--nukex"]

    def test_the_setting_is_read(self):
        assert get_nuke_mode(FakeConfig(nuke_mode="studio")) == "studio"

    def test_other_apps_get_no_nuke_flag(self):
        exe = r"C:\Program Files\Blender Foundation\Blender 4.2\blender.exe"
        assert build_command("blender", exe, r"S:\sh010.blend", "studio") == [exe, r"S:\sh010.blend"]


class TestLaunch:
    def test_launch_opens_the_script_in_nukex(self, program_files, monkeypatch):
        exe = _install(program_files, r"Nuke15.1v3\Nuke15.1.exe")[0]
        cfg = FakeConfig()
        monkeypatch.setattr(dcc_launcher, "ConfigManager", lambda: cfg)
        # Neither database backend has a db_path; the launch used to stop there.
        monkeypatch.setattr(dcc_launcher, "database_manager",
                            SimpleNamespace(backend=SimpleNamespace()))
        calls = []
        monkeypatch.setattr(dcc_launcher.subprocess, "Popen",
                            lambda cmd, **kw: calls.append((cmd, kw)))

        script = str(program_files.parent / "sh010" / "sh010_comp_v003.nk")
        assert DCCLauncher().launch("nuke", 42, file_path=script) is True

        command, kwargs = calls[0]
        assert command == [exe, "--nukex", script]
        assert kwargs["env"]["SLATE_SHOT_ID"] == "42"
        assert "SLATE_DB_PATH" not in kwargs["env"]
        assert kwargs["cwd"] == os.path.dirname(script)
