"""
OpenRV, checked with the real thing.

rv.exe and rvpush.exe are never started (they open windows); rvio reads the
same session files and media arguments, and OpenRV's own Python runs the menu
code that RV would run.
"""

import ast
import re
import subprocess
import textwrap
from pathlib import Path
from types import SimpleNamespace

import pytest

from slate.core import rv_integration
from slate.core.domain import rv_review
from slate.core.domain.rv_feedback import match_shot, read_feedback
from slate.core.rv_integration import RVLauncher
from tests.realtools.conftest import (
    OPENRV, ROOT, RV_PYTHON, make_movie, make_sequence, needs_ffmpeg, needs_rvio, rvio,
)

pytestmark = pytest.mark.realtools

RV_PLUGIN = ROOT / "slate" / "core" / "rv_plugin.py"
MU = OPENRV / "PlugIns" / "Mu"
needs_rv_python = pytest.mark.skipif(not RV_PYTHON.exists(), reason="OpenRV Python not bundled here")


def _captured_launch(monkeypatch, paths, tmp_path):
    """Run rv_review.launch for real, but catch the command instead of starting RV."""
    calls = []
    monkeypatch.setattr(rv_integration, "subprocess",
                        SimpleNamespace(Popen=lambda cmd, **kw: calls.append(cmd)))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "appdata"))
    launcher = RVLauncher()
    launcher.rv_executable = str(OPENRV / "bin" / "rv.exe")
    assert rv_review.launch(paths, launcher=launcher)
    assert len(calls) == 1
    return calls[0]


def _media_args(cmd):
    """What RV is asked to open: the arguments between rv.exe and its own flags."""
    assert Path(cmd[0]).name == "rv.exe", cmd
    i = cmd.index("-pyeval")
    return [a for a in cmd[1:i] if a != "-play"], cmd[i + 1]


def _rendered(tmp_path, media_args):
    out = tmp_path / "rvout"
    out.mkdir()
    code, log = rvio(*media_args, "-o", out / "f.#.jpg", "-v")
    assert code == 0, log
    assert "ERROR" not in log.replace("_socket", ""), log
    return len(list(out.glob("f.*.jpg"))), log


@needs_ffmpeg
@needs_rvio
def test_review_in_rv_playlist_session_plays_in_rvio(monkeypatch, tmp_path):
    """Dashboard "Review in RV" with two departments: the exact .rv RV is given."""
    project = tmp_path / "proj"
    make_sequence(project / "SH010" / "01_Scan" / "v001" / "EXR", count=12)
    # A comp in a folder with a space, an apostrophe and an accent.
    make_movie(project / "SH010" / "07_Comp" / "Output" / "O'Brien é" / "SH010 comp_v002.mov",
               frames=10)
    shot = SimpleNamespace(shot_name="SH010",
                           folder_paths={"scan": "SH010/01_Scan", "comp": "SH010/07_Comp"})
    request = rv_review.build_request(shot, project)
    paths = request.media_paths(["scan", "comp"])
    assert len(paths) == 2 and "%04d" in paths[0]

    cmd = _captured_launch(monkeypatch, paths, tmp_path)
    media, _ = _media_args(cmd)
    assert len(media) == 1 and media[0].endswith(".rv")
    frames, log = _rendered(tmp_path, media)
    assert frames == 12 + 10, log


@needs_ffmpeg
@needs_rvio
def test_open_one_sequence_in_rv_plays_every_frame(monkeypatch, tmp_path):
    """A single plate goes to RV as a printf pattern; RV must find all its frames."""
    pattern = make_sequence(tmp_path / "plate", count=8)
    cmd = _captured_launch(monkeypatch, [str(pattern)], tmp_path)
    assert "-play" in cmd
    media, _ = _media_args(cmd)
    assert media == [str(pattern)]
    frames, log = _rendered(tmp_path, media)
    assert frames == 8, log


def test_rv_command_is_rv_not_rvpush(tmp_path):
    """rvpush has no "replace" command: the old command opened nothing."""
    launcher = RVLauncher.__new__(RVLauncher)
    launcher.rv_executable = "rv.exe"
    cmd = launcher.command(["-play", "a.mov"])
    assert cmd[:3] == ["rv.exe", "-play", "a.mov"]
    assert "replace" not in cmd


@needs_rv_python
def test_pyeval_runs_in_rvs_python_either_way(tmp_path):
    """
    The -pyeval text is one expression - valid whether RV evals or execs it - and,
    run in OpenRV's own Python, it puts slate/core on the path and imports the menu.
    """
    launcher = RVLauncher.__new__(RVLauncher)
    launcher.rv_executable = "rv.exe"
    code = launcher.command(["x.mov"])[-1]
    # Stand-in for the menu module so only the expression itself is under test.
    probe = textwrap.dedent(f"""
        import sys, types
        compile({code!r}, "<pyeval>", "eval"); compile({code!r}, "<pyeval>", "exec")
        mode = types.SimpleNamespace(activate=lambda: print("ACTIVATED"))
        sys.modules["rv_plugin"] = types.SimpleNamespace(createMode=lambda: mode)
        eval({code!r})
        print("ON_PATH", {str(RV_PLUGIN.parent).replace(chr(92), "/")!r} in sys.path)
    """)
    run = subprocess.run([str(RV_PYTHON), "-c", probe], capture_output=True, text=True,
                         timeout=60, cwd=tmp_path)
    assert run.returncode == 0, run.stderr
    assert "ACTIVATED" in run.stdout and "ON_PATH True" in run.stdout


def _rv_api():
    """Names RV's Python offers in rv.commands and rv.extra_commands, from RV's own files."""
    commands = set(re.findall(r"^(\w+)\s+\"", (MU / "commands.mud").read_text(errors="replace"), re.M))
    extra = set(re.findall(r"^\\:\s*(\w+)\s*\(", (MU / "extra_commands.mu").read_text(errors="replace"), re.M))
    return commands, extra


def _plugin_calls():
    used = {"commands": set(), "extra_commands": set()}
    for node in ast.walk(ast.parse(RV_PLUGIN.read_text(encoding="utf-8"))):
        if (isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)
                and node.value.id in used):
            used[node.value.id].add(node.attr)
    return used


@pytest.mark.skipif(not (MU / "commands.mud").exists(), reason="OpenRV Mu files not bundled")
def test_rv_plugin_calls_only_what_rv_offers():
    """
    displayFeedback, sourceFrame and findAnnotatedFrames are extra_commands, not
    commands: called on commands every verdict ended in an AttributeError, the
    frame sent was the timeline's (1, not 1001) and no annotation was exported.
    """
    commands, extra = _rv_api()
    assert {"frame", "exportCurrentFrame"} <= commands and "displayFeedback" in extra
    used = _plugin_calls()
    assert used["commands"] <= commands, used["commands"] - commands
    assert used["extra_commands"] <= extra, used["extra_commands"] - extra


@needs_rv_python
@pytest.mark.skipif(not (MU / "commands.mud").exists(), reason="OpenRV Mu files not bundled")
def test_verdict_from_rv_python_lands_on_the_shot(tmp_path):
    """
    The real menu code, in OpenRV's Python, writes a retake; Slate reads it back
    and finds the shot. RV's command modules are stood in for by ones that only
    answer to the names RV really has.
    """
    commands, extra = _rv_api()
    pkg = tmp_path / "rv"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    media = "C:/proj/SH010/07_Comp/Output/SH010_comp_v002.mov"
    answers = {"frame": 1, "sourcesAtFrame": ["sourceGroup000000_source"],
               "getStringProperty": [media], "sourceFrame": 1001, "findAnnotatedFrames": []}

    def module(names):
        lines = ["shown = []"]
        for name in sorted(names):
            if name == "displayFeedback":
                lines.append("def displayFeedback(*a, **k): shown.append(a[0])")
            else:
                lines.append(f"def {name}(*a, **k): return {answers.get(name)!r}")
        return "\n".join(lines)

    (pkg / "commands.py").write_text(module(commands))
    (pkg / "extra_commands.py").write_text(module(extra))
    (pkg / "rvtypes.py").write_text("class MinorMode:\n    def init(self, *a): pass\n")
    (pkg / "qtutils.py").write_text("def sessionWindow(): return None\n")
    home = tmp_path / "home"
    home.mkdir()
    script = textwrap.dedent(f"""
        import sys
        sys.path[:0] = [{str(tmp_path)!r}, {str(RV_PLUGIN.parent)!r}]
        import rv_plugin, rv.extra_commands as x
        rv_plugin.createMode().send("rejected", "too blue")
        print("SHOWN", x.shown)
    """)
    env = {"USERPROFILE": str(home), "HOME": str(home), "SYSTEMROOT": "C:\\Windows",
           "USERNAME": "supervisor"}
    run = subprocess.run([str(RV_PYTHON), "-c", script], capture_output=True, text=True,
                         timeout=60, cwd=tmp_path, env=env)
    assert run.returncode == 0, run.stderr
    assert "SHOWN ['Slate: REJECTED']" in run.stdout, run.stdout + run.stderr

    feedback = read_feedback(home / ".slate" / "rv_feedback.json")
    assert feedback and feedback.dashboard_status == "Retake"
    assert feedback.frame == 1001 and feedback.note == "too blue"
    shots = [SimpleNamespace(shot_name="SH0100"), SimpleNamespace(shot_name="SH010")]
    assert match_shot(shots, feedback.media_path) is shots[1]


@pytest.mark.skipif(not OPENRV.exists(), reason="OpenRV not bundled here")
@pytest.mark.xfail(strict=True, reason=(
    "FOUND, NOT FIXED: the bundled OpenRV has an empty PlugIns/Python - no 'rv' "
    "package (rvtypes, qtutils, extra_commands) and none of the Python rvpkg "
    "files - so RV's Python, and with it the Slate menu, cannot load. Restore "
    "PlugIns/Python from the OpenRV build."))
def test_bundled_openrv_has_its_python_package():
    python = OPENRV / "PlugIns" / "Python" / "rv"
    for name in ("__init__.py", "rvtypes.py", "qtutils.py", "extra_commands.py"):
        assert (python / name).exists(), name


def test_installed_build_ships_the_rv_menu_as_a_file():
    """
    RV imports rv_plugin.py from the folder rv_integration lives in. PyInstaller
    keeps Slate's code compiled inside the app, so the build has to copy this one
    out as a plain file into that same folder.
    """
    spec = (ROOT / "deployment" / "Slate.spec").read_text(encoding="utf-8")
    assert "(R('slate', 'core', 'rv_plugin.py'), 'slate/core')" in spec
    assert Path(rv_integration.__file__).parent == RV_PLUGIN.parent
