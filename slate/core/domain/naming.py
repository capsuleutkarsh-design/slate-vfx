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

import re
import unicodedata
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
        return f"{what} cannot contain {shown}."
    if text != text.strip():
        return f"{what} cannot start or end with a space."
    if text.endswith("."):
        return f"{what} cannot end with a dot."
    if text.split(".")[0].upper() in RESERVED_NAMES:
        return f"'{text}' is a name Windows keeps for itself."
    if len(text) > max_length:
        return f"{what} is too long ({len(text)} characters, max {max_length})."
    return None


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
    text = re.sub(r"[^A-Za-z0-9_\-]+", "_", text)
    text = re.sub(r"_+", "_", text).strip("_-")
    if not text:
        return raw
    return text.upper()
