"""
Names people type that become folders and files.

Build & Ingest, its stitch dialog, the template editor and CAP Rename all take
a name from a person and turn it into something on disk. Each used to clean the
name quietly - '..' and '/' included - so a typo built a shot tree outside the
project, a replacement with a slash moved files into a new sub-folder, and a
project code with a colon was changed under the coordinator's feet. The rules
live here once, and they *refuse* rather than repair: the person is told what
is wrong and fixes it.
"""

from __future__ import annotations

import os
import re
import unicodedata
from pathlib import Path
from typing import Optional

# Characters Windows will not take in a file or folder name, plus controls.
FORBIDDEN_CHARACTERS = '<>:"|?*'

# Device names Windows reserves, with or without an extension.
RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL", "CLOCK$", "CONFIG$", "CONIN$", "CONOUT$",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}

# Long enough for any real shot or file name, short enough that a deep shot
# tree still fits a server path.
MAX_NAME_LENGTH = 200


def _shown(characters) -> str:
    return " ".join(sorted(set(characters)))


def name_problem(name, what: str = "A name", max_length: int = MAX_NAME_LENGTH) -> Optional[str]:
    """
    Why `name` cannot be used as one file or folder name, or None when it can.

    The answer is a sentence a person can act on: "A shot name cannot contain
    a folder (/ or \\)." `what` starts it ("A shot name", "The project code").
    """
    text = "" if name is None else str(name)
    if not text.strip():
        return f"{what} cannot be empty."
    if "/" in text or "\\" in text:
        return f"{what} cannot contain a folder (/ or \\)."
    if ".." in text:
        return f"{what} cannot contain '..'."
    bad = [c for c in text if c in FORBIDDEN_CHARACTERS or ord(c) < 32]
    if bad:
        shown = _shown(c if ord(c) >= 32 else "a control character" for c in bad)
        return f"{what} cannot contain these characters: {shown}"
    if text != text.strip():
        return f"{what} cannot start or end with a space."
    if text.endswith("."):
        return f"{what} cannot end with a dot."
    if text.split(".")[0].upper() in RESERVED_NAMES:
        return f"'{text}' is a name Windows keeps for itself."
    if len(text) > max_length:
        return f"{what} is too long ({len(text)} characters, max {max_length})."
    return None


# Shot names are stricter than any file name: they are typed into Nuke
# scripts, render paths and client sheets, so letters, digits, '_', '-' and
# '.' only, no spaces, and short enough for a deep shot tree.
SHOT_NAME_MAX = 64
_SHOT_ALLOWED = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-]*$")


def shot_name_problem(name, what: str = "Shot name") -> Optional[str]:
    """
    Why `name` cannot be a shot (or reel) on the dashboard, or None when it can.

    The one rule for every way a shot is made - Add Shots on the dashboard,
    Build & Ingest (pre-flight, worker and registration) and Create shots
    from a bid - so none of them can create a name another one refuses.
    """
    text = "" if name is None else str(name)
    if not text.strip():
        return f"{what} is empty."
    if len(text) > SHOT_NAME_MAX:
        return f"{what} '{text[:20]}…' is {len(text)} characters; the limit is {SHOT_NAME_MAX}."
    # One sentence for every character rule, so the person sees all of them
    # at once (a name with a space and a '/' used to hear about the space only).
    if ".." in text or not _SHOT_ALLOWED.match(text):
        return (f"{what} '{text}' can only use letters, digits, _ - and . (English A-Z only) "
                f"and must start with a letter or digit (no spaces, / or ..).")
    # What is left of the file-name rules: Windows device names, a final dot.
    return name_problem(text, what)


def folder_path_problem(path, what: str = "A folder") -> Optional[str]:
    """
    Like name_problem for a template entry such as '02_Dmp/Work/PSD': every
    part is checked, the separator itself is allowed, nothing may climb out.
    """
    text = "" if path is None else str(path).strip()
    if not text:
        return f"{what} cannot be empty."
    parts = [p for p in re.split(r"[\\/]", text)]
    if any(not p.strip() for p in parts):
        return f"{what} has an empty part ('{text}')."
    for part in parts:
        problem = name_problem(part, what)
        if problem:
            return problem
    return None


def path_inside(root, *parts) -> Path:
    """
    root joined with parts, refused (ValueError) if it would land outside root.

    New names are checked when typed, but a name stored before those checks -
    'SH010/../../x' - is not, and folders are built from stored names. Every
    such path goes through here. Lexical (no symlinks followed), so a project
    whose 05_Reels is a junction to another drive still works.
    """
    base = os.path.abspath(str(root or "."))
    full = os.path.abspath(os.path.join(base, *(str(p) for p in parts)))
    try:
        inside = os.path.normcase(os.path.commonpath([base, full])) == os.path.normcase(base)
    except ValueError:              # another drive
        inside = False
    if not inside:
        raise ValueError(f"'{os.path.join(*(str(p) for p in parts))}' would be outside "
                         f"the project folder {base}, so it was not used. Rename the shot or project.")
    return Path(full)


# --------------------------------------------------------------- shot names
_BRACKETED = re.compile(r"[\(\[\{][^\)\]\}]*[\)\]\}]")
_CLEAN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_\-]*$")


def is_clean_shot_name(name: str) -> bool:
    """Letters, digits, '_' and '-' only: a name the pipeline can use as it is."""
    return bool(_CLEAN.match(str(name or "")))


def normalise_shot_name(name: str) -> str:
    """
    A shot name the pipeline can use, proposed from a client's folder name.

    A clean name is kept exactly as delivered (SH010, sh_010_bg). Anything
    else - spaces, brackets, punctuation - is tidied and upper-cased the way
    shot codes are written: 'sh 060 (client)' -> 'SH_060'. The client's
    folder name is kept beside it (source_folder), so nothing is lost.
    """
    raw = str(name or "").strip()
    if is_clean_shot_name(raw):
        return raw
    text = _BRACKETED.sub(" ", raw)
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    if is_clean_shot_name(text):
        return text                      # only accents came off: 'Ünïcode_030' -> 'Unicode_030'
    text = "".join(_spelled(c) for c in text)
    text = re.sub(r"[^A-Za-z0-9_\-]+", "_", text)
    text = re.sub(r"_+", "_", text).strip("_-")
    if not text:
        return raw
    return text.upper()


def _spelled(char: str) -> str:
    """
    A letter of another script by its Unicode name, so a Hindi or Cyrillic
    folder name keeps telling shots apart ('शॉट 010' -> 'SHATTA_010', not a
    bare '010' that clashes with every other one). Not a transliteration -
    the pre-flight marks such names for a person to check.
    """
    if char.isascii():
        return char
    if unicodedata.category(char)[0] == "M":
        return ""                        # vowel signs and marks
    words = unicodedata.name(char, "").split(" LETTER ")
    return words[1].split()[-1] if len(words) == 2 else ""
