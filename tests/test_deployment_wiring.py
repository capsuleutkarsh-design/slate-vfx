"""
The installers and the build agreeing about what gets shipped.

Two failures here reached a studio machine, and neither of them is visible in
any Python module.

The first: the server installer takes dist/Slate_Server.exe, and the build
pipeline only ever built dist/Slate - a different layout from a different spec.
So the server installer compiled without complaint against whatever one-file
build happened to be left in dist from an earlier session, and shipped a server
older than the client beside it.

The second: the server was built with no settings bundled at all, so it had no
database password, could not create the accounts, and hardened the cluster
anyway. These are text assertions on purpose. The thing being checked is
whether two files that never import each other still agree.
"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
DEPLOY = ROOT / "deployment"
sys.path.insert(0, str(ROOT / "tools"))

INSTALLERS = ("setup_slate_client.iss", "setup_slate_ops.iss",
              "setup_slate_server.iss")


def read(path):
    return (DEPLOY / path).read_text(encoding="utf-8")


# ------------------------------------------------------------ what is built

def test_the_pipeline_builds_the_executable_the_server_installer_installs():
    """
    setup_slate_server.iss installs dist\\Slate_Server.exe. If nothing in the
    build produces it, the installer picks up a stale one and says nothing.
    """
    pipeline = (ROOT / "tools" / "build_pipeline.py").read_text(encoding="utf-8")
    assert "Slate_Server.spec" in pipeline, \
        "the build must produce dist/Slate_Server.exe, not inherit it"
    assert "Slate.spec" in pipeline


def test_the_server_build_ships_the_settings():
    """
    Without these the frozen server has no database password. It then creates no
    accounts, and the cluster it hardens cannot be logged into by anything,
    itself included.
    """
    for spec in ("Slate_Server.spec", "Slate.spec"):
        text = read(spec)
        assert "'default_config.json'" in text, \
            "%s ships a server with no settings in it" % spec


def test_the_server_spec_still_ships_postgres():
    assert "'bin'" in read("Slate_Server.spec")


# ----------------------------------------------------- what gets uninstalled

@pytest.mark.parametrize("name", INSTALLERS)
def test_every_installer_offers_to_remove_its_data(name):
    text = read(name)
    assert '#include "inc_slate_data.iss"' in text
    assert "ShouldRemoveData" in text, \
        "%s uninstalls without ever offering to clear what it leaves behind" % name
    assert "usPostUninstall" in text


@pytest.mark.parametrize("name", INSTALLERS)
def test_no_installer_carries_its_own_copy_of_the_delete_helpers(name):
    """
    Two of them did, unguarded. One guarded implementation, in the include.
    """
    text = read(name)
    assert "procedure TryDeleteDirIfExists" not in text
    assert "procedure TryDeleteFileIfExists" not in text


def test_the_shared_include_guards_what_it_deletes():
    text = read("inc_slate_data.iss")
    assert "function LooksDeletable" in text
    for constant in ("{localappdata}", "{%USERPROFILE}", "{pf}", "{win}"):
        assert constant in text, \
            "a bare %s must never be handed to DelTree" % constant


def test_keeping_the_data_is_what_an_unattended_uninstall_does():
    """The choice that cannot be taken back is not the one a silent run makes."""
    text = read("inc_slate_data.iss")
    assert "UninstallSilent" in text
    assert "PURGEDATA" in text and "KEEPDATA" in text
    assert "MB_DEFBUTTON2" in text, "the default button must be No"


def test_only_the_server_offers_to_delete_the_database():
    """
    A workstation cannot reach the studio's data, so its uninstaller must not
    imply that it is deleting any.
    """
    assert "Slate_Central" in read("setup_slate_server.iss")
    for name in ("setup_slate_client.iss", "setup_slate_ops.iss"):
        assert "Slate_Central\\" not in read(name), \
            "%s must not delete the server's database" % name


def test_the_server_uninstall_stops_the_pooler_as_well_as_postgres():
    """
    A pooler left running from an older install holds its port and serves a
    stale configuration to every client, and it survived every taskkill here.
    """
    text = read("setup_slate_server.iss")
    assert "pgbouncer.exe" in text
    assert "postgres.exe" in text


# ------------------------------------------------------ constants that exist

# Every Setup constant these scripts are allowed to expand. ExpandConstant
# raises on a name Inno does not define, and an exception inside an uninstall
# step aborts the whole step - so one misspelling ({userprofile}, which Inno has
# no such thing as) meant the guard did not run, nothing was deleted, Setup
# showed "Internal error: Unknown constant", and the uninstall still reported
# success. Nothing else would have caught that.
KNOWN_CONSTANTS = {
    "app", "sys", "win", "localappdata", "userappdata", "commonappdata",
    "userdocs", "commondocs", "pf", "pf32", "pf64", "commonpf", "commonpf32",
    "autoprograms", "autodesktop", "userdesktop", "commonprograms", "tmp",
    "src", "srcexe", "uninstallexe", "cm", "%USERPROFILE",
}


@pytest.mark.parametrize("name", INSTALLERS + ("inc_slate_data.iss",))
def test_every_setup_constant_is_one_inno_defines(name):
    import re

    text = read(name)
    used = set()
    for call in re.findall(r"ExpandConstant\('([^']*)'\)", text):
        used.update(re.findall(r"\{([A-Za-z%][A-Za-z0-9_%]*)", call))

    unknown = sorted(used - KNOWN_CONSTANTS)
    assert not unknown, (
        "%s expands %s, which Inno does not define. ExpandConstant raises on "
        "an unknown name and takes the rest of the step with it."
        % (name, unknown))


def test_the_guard_survives_a_constant_it_does_not_recognise():
    """
    Belt and braces for the above: even if one slips through, it must not be
    able to abort an uninstall again.
    """
    text = read("inc_slate_data.iss")
    assert "function SafeConstant" in text
    assert "except" in text, "the constant lookup has to be inside a try/except"


def test_the_purge_does_not_fight_the_running_uninstaller():
    """
    unins000.exe runs from the folder being cleared. Windows will not delete a
    running program, so handing it to DelTree produced a failure report about a
    file that was never a problem - an error dialog at the end of every
    uninstall.
    """
    text = read("inc_slate_data.iss")
    assert "function IsUninstallerFile" in text
    assert "procedure PurgeDirectory" in text

    for name in INSTALLERS:
        script = read(name)
        assert "PurgeDirectory(ExpandConstant('{app}'))" in script, \
            "%s still DelTrees its own program folder" % name


# ------------------------------------------------ a build that fails out loud

def test_a_failed_server_build_says_what_went_wrong():
    """
    The first version of this step printed "did not build" and threw away
    everything PyInstaller had said about why - which is the same fault it
    exists to prevent: something failing without saying what it could not do.
    """
    pipeline = (ROOT / "tools" / "build_pipeline.py").read_text(encoding="utf-8")

    assert "The last lines PyInstaller printed" in pipeline
    assert "collections.deque" in pipeline, "the output has to be kept, not just printed"
    assert "is in use" in pipeline, "name the usual cause"


def test_the_build_checks_the_executable_it_claims_to_have_made():
    """
    A clean exit code is not the same as a new file. Packaging an executable
    from a previous build is exactly the failure this whole step exists to
    stop, so the check is on the file rather than on the return code.
    """
    pipeline = (ROOT / "tools" / "build_pipeline.py").read_text(encoding="utf-8")

    assert "stamp_before" in pipeline
    assert "was not rewritten" in pipeline
    assert "os.path.abspath(server_spec)" in pipeline, \
        "the spec path must not depend on where the previous build left the cwd"


# ------------------------------------ the build and the code agreeing on paths

def test_the_client_looks_where_the_build_puts_its_settings():
    """
    The spec ships slate/default_config.json, so in a frozen build it lands
    under a "slate" folder. Every path GlobalConfig looked at was the bare
    filename at the top - so no frozen client ever read its own bundled
    settings. It worked only on machines that already had a per-machine config;
    a workstation installed from scratch had no password, no user and no
    database name, fell back to local SQLite, and never reached the studio.
    """
    spec = read("Slate.spec")
    assert "(R('slate', 'default_config.json'), 'slate')" in spec, \
        "if the build stops shipping it under slate/, fix the search below too"

    source = (ROOT / "slate" / "core" / "infra"
              / "global_config.py").read_text(encoding="utf-8")
    assert 'Path(sys._MEIPASS) / "slate" / "default_config.json"' in source
    assert 'Path(base_dir) / "slate" / "default_config.json"' in source


# ------------------------------------------------- the faults fixed in Sep 2026

def test_the_server_is_built_exactly_once():
    """
    A second, flag-driven build of the server used to follow the verified spec
    build and overwrite it with an executable that had no settings - and left
    a generated spec at the project root that failed every later build.
    """
    pipeline = (ROOT / "tools" / "build_pipeline.py").read_text(encoding="utf-8")
    live = [l for l in pipeline.splitlines() if l.strip() and not l.strip().startswith("#")]
    assert not any("def build_server_release" in l for l in live)
    assert not any("build_server_release()" in l for l in live)


def test_the_data_copy_is_its_own_step_not_a_side_effect_of_the_check():
    """
    The copy of Olive and the database scripts into dist/Slate sat inside the
    settings check, after its early returns, so an unreadable bundle skipped
    the copy as well and nothing said so.
    """
    import ast

    source = (ROOT / "tools" / "build_pipeline.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    functions = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}

    assert "_copy_unmanaged_data" in functions
    check = functions["_check_server_carries_its_settings"]
    calls = {n.func.attr for n in ast.walk(check)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
    assert "copytree" not in calls, "the check must not also be the copy"
    assert "_copy_unmanaged_data" in ast.dump(functions["build_onedir"])


def test_nothing_is_shipped_twice():
    """
    ffmpeg goes into _internal/slate/bin through the spec; copying slate/bin
    beside it doubled 190 MB in every installer. slate_server/bin is inside the
    one-file server and excluded by both client installers, so copying it into
    dist/Slate was 340 MB per build that nothing read.
    """
    pipeline = (ROOT / "tools" / "build_pipeline.py").read_text(encoding="utf-8")
    live = [l for l in pipeline.splitlines() if l.strip() and not l.strip().startswith("#")]
    assert not any("copy_if_exists('slate/bin'" in l for l in live)
    assert not any("copy_if_exists('slate_server/bin'" in l for l in live)

    spec = read("Slate_Server.spec")
    assert "'pgAdmin 4'" in spec, "the server does not run pgAdmin; do not unpack it on every start"


def test_the_server_installer_ships_the_updater():
    """
    The sidecar looks for SlateUpdater.exe beside the running executable. A
    server installed without it stages every update and applies none.
    """
    assert "SlateUpdater.exe" in read("setup_slate_server.iss")


@pytest.mark.parametrize("name", INSTALLERS)
def test_programs_are_not_installed_into_the_settings_folder(name):
    """
    {localappdata}\\Slate is where GlobalConfig keeps this machine's config,
    the offline database and the cache. Installing a program there meant every
    upgrade wiped them.
    """
    text = read(name)
    line = next(l for l in text.splitlines() if l.startswith("DefaultDirName="))
    assert line.startswith("DefaultDirName={localappdata}\\Programs\\"), line


def test_a_studio_upgrade_leaves_the_settings_folder_alone():
    text = read("setup_slate_client.iss")
    live = [l for l in text.splitlines() if l.strip() and not l.strip().startswith("//")]
    assert not any("TryDeleteDirIfExists(ExpandConstant('{localappdata}\\{#MyAppName}'))" in l
                   for l in live)
    assert "RemoveOldProgramFiles" in text
    assert "procedure RemoveOldProgramFiles" in read("inc_slate_data.iss")


def test_update_packages_carry_the_real_version():
    """
    "latest" is newer than anything to the update checker, so a package that
    said so was offered again after every install, forever.
    """
    tool = (ROOT / "tools" / "build_update_package.py").read_text(encoding="utf-8")
    assert 'version="latest"' not in tool
    assert "current_version" in tool
    assert "returncode" in tool, "a failed server build must not be packaged"


def test_one_writer_for_the_version():
    from bump_version import ISS_FILES, INIT_FILE, PYPROJECT_FILE

    assert {p.name for p in ISS_FILES} == set(INSTALLERS)
    assert INIT_FILE.name == "__init__.py"
    assert PYPROJECT_FILE.name == "pyproject.toml"
    assert "set_version" in (ROOT / "tools" / "build_pipeline.py").read_text(encoding="utf-8")


def test_postgres_is_pinned_to_the_version_the_clusters_run():
    import json

    manifest = json.loads((ROOT / "setup" / "components.json").read_text(encoding="utf-8"))
    postgres = next(c for c in manifest["components"] if c["name"] == "postgresql")
    assert "postgresql-14." in postgres["url"], \
        "every data directory is PG_VERSION 14; newer binaries cannot open them"
    assert any(c["name"] == "ffprobe" for c in manifest["components"]), \
        "the specs and ResourcePathManager expect ffprobe beside ffmpeg"
