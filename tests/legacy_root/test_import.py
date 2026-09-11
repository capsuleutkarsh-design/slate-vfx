"""
A leftover from before there was a test suite.

It used to open a file called crash_log.txt in the working directory and write
"Import successful!" into it. It asserted nothing, so it could not fail, and
the only thing it actually did was drop a file in the project root every time
anybody ran the tests - which is why crash_log.txt kept reappearing after being
deleted.

What it was reaching for is worth keeping: that the package imports at all. So
that is what it checks now, without writing anything anywhere.
"""

import importlib


def test_the_package_imports():
    """The one thing the old crash_log file was really reporting."""
    assert importlib.import_module("slate") is not None


def test_the_entry_points_import():
    """
    Each shell's entry module, imported rather than run.

    An import error here is the failure that used to show up as a blank window
    on somebody's machine and nowhere else.
    """
    for name in ("slate.core.infra.database_manager",
                 "slate.core.infra.global_config",
                 "slate.core.domain.leave_policy"):
        assert importlib.import_module(name) is not None, name
