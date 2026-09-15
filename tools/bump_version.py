"""
The one place the version number is written.

It is recorded in five files - slate/__init__.py, pyproject.toml and the three
installer scripts in deployment/ - and until now two tools wrote to different
subsets of them. The build pipeline rewrote __init__.py and the installers and
left pyproject.toml behind; this script rewrote __init__.py and pyproject.toml
and a deployment/setup_slate.iss that no longer existed. The three copies
drifted, and nothing said which one was right.

    python tools/bump_version.py "BETA 2.0.27"
    python tools/bump_version.py            # asks

The build pipeline calls set_version() with the version typed into the
console, so a build and a bump write the same five files the same way.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

INIT_FILE = Path("slate") / "__init__.py"
PYPROJECT_FILE = Path("pyproject.toml")
ISS_FILES = (
    Path("deployment") / "setup_slate_client.iss",
    Path("deployment") / "setup_slate_ops.iss",
    Path("deployment") / "setup_slate_server.iss",
)

# What each file calls it, and how it is written back. pyproject.toml gets the
# same version in the form packaging tools accept - see pep440().
_RULES = (
    (INIT_FILE, r'__version__\s*=\s*["\'][^"\']*["\']', '__version__ = "{v}"'),
    (PYPROJECT_FILE, r'(?m)^version\s*=\s*"[^"]*"', 'version = "{p}"'),
) + tuple(
    (iss, r'#define MyAppVersion "[^"]*"', '#define MyAppVersion "{v}"')
    for iss in ISS_FILES
)


def pep440(version: str) -> str:
    """
    The product version as a Python package version.

    The product is versioned the way the studio talks about it - "BETA 2.0.26"
    - and that string goes into the installers and the login screen as is.
    pyproject.toml has to hold a PEP 440 version or pip and uv refuse the
    project, so the same release is written there as 2.0.26b0. The two are
    the same version; only the spelling differs.
    """
    text = str(version).strip()
    lowered = text.lower()
    suffix = ""
    for word, tag in (("beta", "b0"), ("alpha", "a0"), ("rc", "rc0")):
        if lowered.startswith(word):
            text = text[len(word):].strip(" -_")
            suffix = tag
            break
    if re.fullmatch(r"\d+(\.\d+)*", text):
        return text + suffix
    return str(version).strip()


def current_version(root: Path = ROOT) -> str:
    """The version slate/__init__.py records, or 0.0.0 if it has none."""
    try:
        text = (root / INIT_FILE).read_text(encoding="utf-8")
    except OSError:
        return "0.0.0"
    match = re.search(r'__version__\s*=\s*["\']([^"\']+)["\']', text)
    return match.group(1) if match else "0.0.0"


def recorded_versions(root: Path = ROOT) -> dict:
    """Every file's idea of the version, so a drift can be seen before a bump."""
    found = {}
    for rel, pattern, _template in _RULES:
        path = root / rel
        if not path.exists():
            found[rel] = None
            continue
        match = re.search(pattern, path.read_text(encoding="utf-8"))
        if not match:
            found[rel] = None
            continue
        value = re.search(r'"([^"]*)"|\'([^\']*)\'', match.group(0))
        found[rel] = (value.group(1) or value.group(2)) if value else None
    return found


def set_version(version: str, root: Path = ROOT) -> list:
    """
    Write the version into every file that carries it. Returns the paths written.

    A file that is missing, or that has no version line to replace, is reported
    rather than skipped silently: a build that stamps four of five files is the
    drift this module exists to end.
    """
    version = str(version).strip()
    if not version:
        raise ValueError("a version has to be given")

    written = []
    problems = []
    for rel, pattern, template in _RULES:
        path = root / rel
        if not path.exists():
            problems.append(f"{rel} does not exist")
            continue
        text = path.read_text(encoding="utf-8")
        replacement = template.format(v=version, p=pep440(version))
        new_text, count = re.subn(pattern, lambda _m: replacement, text, count=1)
        if count == 0:
            problems.append(f"{rel} has no version line to replace")
            continue
        if new_text != text:
            path.write_text(new_text, encoding="utf-8")
        written.append(path)

    if problems:
        raise RuntimeError("the version could not be set everywhere:\n  "
                           + "\n  ".join(problems))
    return written


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)

    print("--- Slate version ---")
    drift = False
    current = current_version()
    for rel, value in recorded_versions().items():
        print(f"  {str(rel):40s} {value or 'not found'}")
        if value not in (current, pep440(current)):
            drift = True
    if drift:
        print("WARNING: the files disagree about the version.")

    new_version = argv[0] if argv else input(f"New version [{current}]: ").strip()
    if not new_version:
        print("Nothing changed.")
        return 0

    for path in set_version(new_version):
        print(f"[UPDATED] {path.relative_to(ROOT)}")
    print(f"\nEvery file now says {new_version}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
