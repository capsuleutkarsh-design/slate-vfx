"""
Finding the media that belongs to a shot.

A dashboard shot knows where its folders are - ``folder_paths`` maps "scan",
"comp", "prep" and every other department to a path inside the project. What it
does not know is which of those folders actually contain something to look at.

Both the review player and the Olive timeline need the same answer: given a
shot and a department, what is the one thing a person would want to watch, and
which departments have anything at all? That answer lives here once, so the
picker in the review button and the tracks in the timeline can never disagree.

Scans are versioned (``01_Scan/v003/EXR``), renders are not, so a scan resolves
to the newest version unless an older one is asked for by name.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

from ut_vfx.core.domain.departments import load_departments


logger = logging.getLogger(__name__)

# What we are willing to hand to a player, best first. A movie is one file and
# always plays; an image sequence needs the frames to be found first.
MOVIE_SUFFIXES = (".mov", ".mp4", ".mxf", ".avi")
SEQUENCE_SUFFIXES = (".exr", ".dpx", ".tif", ".tiff", ".jpg", ".jpeg", ".png")

# Files that are in the folder but are not the delivery.
_JUNK_PREFIXES = ("._", "~$", ".")

_VERSION_DIR = re.compile(r"^v(\d+)$", re.IGNORECASE)
_FRAME_IN_NAME = re.compile(r"^(?P<head>.*?)(?P<frame>\d+)(?P<tail>\.[^.]+)$")


@dataclass
class MediaClip:
    """One thing a person can watch."""

    path: Path
    department: str = ""
    label: str = ""
    is_sequence: bool = False
    first_frame: int = 0
    last_frame: int = 0
    scan_version: str = ""

    @property
    def frame_count(self) -> int:
        if not self.is_sequence:
            return 0
        return max(0, self.last_frame - self.first_frame + 1)

    def exists(self) -> bool:
        try:
            # A sequence is stored as a printf pattern, which never exists as
            # a file of its own - its folder is what has to be there.
            if "%" in str(self.path):
                return self.path.parent.is_dir()
            return self.path.exists()
        except OSError:
            return False


def _is_junk(path: Path) -> bool:
    name = path.name
    return any(name.startswith(prefix) for prefix in _JUNK_PREFIXES)


def _media_files(folder: Path, suffixes) -> List[Path]:
    try:
        return sorted(
            f for f in folder.iterdir()
            if f.is_file() and not _is_junk(f)
            and f.suffix.lower() in suffixes
        )
    except (PermissionError, OSError):
        return []


def _sequence_from_files(files: List[Path]) -> Optional[MediaClip]:
    """
    Turn a folder of numbered frames into one clip Olive and RV both accept.

    Frames become ``name.%04d.exr``; the padding is taken from the real file
    names rather than assumed, because a four-digit assumption silently breaks
    a show numbering past 9999.
    """
    groups: Dict[tuple, List[int]] = {}
    for f in files:
        match = _FRAME_IN_NAME.match(f.name)
        if not match:
            continue
        key = (match.group("head"), len(match.group("frame")), match.group("tail"))
        groups.setdefault(key, []).append(int(match.group("frame")))

    if not groups:
        return None

    # The real sequence is the one with the most frames.
    (head, pad, tail), frames = max(groups.items(), key=lambda kv: len(kv[1]))
    folder = files[0].parent
    pattern = f"{head}%0{pad}d{tail}"

    return MediaClip(
        path=folder / pattern,
        is_sequence=True,
        first_frame=min(frames),
        last_frame=max(frames),
    )


def _best_clip_in_folder(folder: Path) -> Optional[MediaClip]:
    """
    The one thing worth watching in this folder.

    A movie wins over a sequence: it is what a review is normally run from, and
    it plays without waiting on a frame scan.
    """
    if not folder.is_dir():
        return None

    movies = _media_files(folder, MOVIE_SUFFIXES)
    if movies:
        # Newest, so a re-render is what gets reviewed.
        newest = max(movies, key=lambda f: f.stat().st_mtime)
        return MediaClip(path=newest, label=newest.name)

    frames = _media_files(folder, SEQUENCE_SUFFIXES)
    if frames:
        clip = _sequence_from_files(frames)
        if clip:
            clip.label = clip.path.name
            return clip
        return MediaClip(path=frames[0], label=frames[0].name)

    return None


def _search_folders(root: Path) -> List[Path]:
    """
    Where to look inside a department folder, nearest first.

    Departments keep their reviewable output in ``Output``, sometimes split
    again (``Output/Anim``, ``Output/Shape``). The department root itself is
    included last so a studio that drops a MOV straight in still works.
    """
    candidates: List[Path] = []
    output = root / "Output"
    if output.is_dir():
        candidates.append(output)
        try:
            candidates.extend(sorted(d for d in output.iterdir() if d.is_dir()))
        except (PermissionError, OSError):
            pass
    candidates.append(root)
    return candidates


def scan_versions(scan_root: Path) -> List[str]:
    """Every scan version in a shot's scan folder, oldest first."""
    if not scan_root.is_dir():
        return []
    try:
        versions = [d.name for d in scan_root.iterdir()
                    if d.is_dir() and _VERSION_DIR.match(d.name)]
    except (PermissionError, OSError):
        return []
    return sorted(versions, key=lambda v: int(_VERSION_DIR.match(v).group(1)))


def resolve_scan(scan_root: Path, version: str = "") -> Optional[MediaClip]:
    """
    The plate to review for this shot.

    Defaults to the newest scan version: when a client re-delivers a shot, the
    new plate is the one that matters, and reviewing the old one silently is
    the kind of mistake that reaches a client.
    """
    scan_root = Path(scan_root)
    versions = scan_versions(scan_root)

    if version and version in versions:
        chosen = version
    else:
        # A version that is not there falls back to the newest one. Showing the
        # plate that exists beats showing nothing and looking broken.
        chosen = versions[-1] if versions else ""

    if not chosen:
        # An un-versioned scan folder, from before scan versioning existed.
        clip = _find_in_tree(scan_root)
        if clip:
            clip.department = "scan"
        return clip

    clip = _find_in_tree(scan_root / chosen)
    if clip:
        clip.department = "scan"
        clip.scan_version = chosen
    return clip


def _find_in_tree(root: Path) -> Optional[MediaClip]:
    """First playable media at or just below this folder."""
    if not root.is_dir():
        return None

    direct = _best_clip_in_folder(root)
    if direct:
        return direct

    try:
        children = sorted(d for d in root.iterdir() if d.is_dir())
    except (PermissionError, OSError):
        return None

    for child in children:
        clip = _best_clip_in_folder(child)
        if clip:
            return clip
    return None


def resolve_department(dept_root: Path, department: str = "") -> Optional[MediaClip]:
    """The render to review for one department, or None if it has produced none."""
    dept_root = Path(dept_root)
    for folder in _search_folders(dept_root):
        clip = _best_clip_in_folder(folder)
        if clip:
            clip.department = department
            return clip
    return None


def shot_folder(shot, project_root, key: str) -> Optional[Path]:
    """
    Absolute path of one of a shot's folders.

    ``folder_paths`` holds paths relative to the project root, which is what
    makes a project survive being moved or mounted on a different drive letter.
    """
    paths = getattr(shot, "folder_paths", None) or {}
    if not isinstance(paths, dict):
        return None

    raw = paths.get(key)
    if not raw:
        return None

    candidate = Path(raw)
    if candidate.is_absolute():
        return candidate
    if not project_root:
        return None
    return Path(project_root) / candidate


def annotation_folder(shot, project_root=None, folder_resolver=None
                      ) -> Optional[Path]:
    """
    Where this shot's review annotations are kept.

    Projects made before annotations were filed have no "annotation" entry in
    their folder template, so rather than needing a migration the folder is
    worked out from the scan folder's parent - the shot's own root - which
    every project has.
    """
    if folder_resolver is not None:
        try:
            found = folder_resolver(shot, "annotation")
        except Exception as exc:
            logger.debug("Annotation folder lookup failed: %s", exc)
            found = None
        if found:
            return Path(found)

    direct = shot_folder(shot, project_root, "annotation")
    if direct:
        return direct

    # Derive it from the shot root instead.
    scan_root = None
    if folder_resolver is not None:
        try:
            resolved = folder_resolver(shot, "scan")
            scan_root = Path(resolved) if resolved else None
        except Exception:
            scan_root = None
    if scan_root is None:
        scan_root = shot_folder(shot, project_root, "scan")

    if scan_root is None:
        return None
    return scan_root.parent / "00_Annotation"


def available_media(shot, project_root=None, include_empty: bool = False,
                    folder_resolver=None) -> Dict[str, MediaClip]:
    """
    Everything there is to watch for this shot, keyed by department.

    "scan" is always the plate. Every other key is a department from
    departments.json that has actually rendered something, so a picker built
    from this never offers a department with nothing behind it.

    ``folder_resolver`` is a ``(shot, key) -> path`` callable. The dashboard
    passes its own, which knows how to cope with the older projects whose folder
    names do not match the template exactly; without one, the shot's own
    ``folder_paths`` are used.
    """
    def resolve(key):
        if folder_resolver is not None:
            try:
                found_path = folder_resolver(shot, key)
            except Exception as exc:
                logger.debug("Folder resolver failed for %s: %s", key, exc)
                found_path = None
            if found_path:
                return Path(found_path)
        return shot_folder(shot, project_root, key)

    found: Dict[str, MediaClip] = {}

    scan_root = resolve("scan")
    if scan_root:
        clip = resolve_scan(scan_root)
        if clip:
            found["scan"] = clip

    for dept in load_departments():
        root = resolve(dept.key)
        if not root:
            continue
        clip = resolve_department(root, dept.key)
        if clip:
            found[dept.key] = clip
        elif include_empty:
            found[dept.key] = MediaClip(path=root, department=dept.key)

    return found


def department_label(key: str) -> str:
    """The name a person knows a department by."""
    if key == "scan":
        return "Scan"
    for dept in load_departments():
        if dept.key == key:
            return dept.name or key.title()
    return key.title()
