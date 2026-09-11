"""
Building an Olive timeline out of what the dashboard knows.

The lineup starts as the plates, in reel order: that is the edit, and it exists
before any work is done. As renders arrive they are laid on their own layer
directly beneath the plate they came from, so scrubbing down a column shows the
same moment of the same shot at each stage.

    layer 1   Scan     the plate, always present
    layer 2   Comp
    layer 3   Prep
    layer 4   Deage

One project produces a timeline per reel and one holding every reel, because
both get used: a reel to review a section, the combined one to see the show.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from slate.core.domain.olive_bridge import OliveBridge
from slate.core.domain.shot_media import MediaClip, available_media


logger = logging.getLogger(__name__)

# Which department goes on which layer, top down. The plate is always layer 1.
TRACK_LAYOUT: Tuple[Tuple[str, str], ...] = (
    ("Scan", "scan"),
    ("Comp", "comp"),
    ("Prep", "prep"),
    ("Deage", "deage"),
)

# When a shot has no frame range yet, a clip still needs a length. This is only
# a placeholder for the timeline; the real range replaces it as soon as the
# ingest has recorded one.
FALLBACK_DURATION = 100

_TRAILING_NUMBER = re.compile(r"(\d+)")


@dataclass
class LineupShot:
    """One shot as Olive needs to see it: a name, a length, and its layers."""

    name: str
    reel: str = ""
    fps: float = 24.0
    frame_range: Optional[Tuple[int, int]] = None
    paths: Dict[str, Path] = field(default_factory=dict)

    def __getattr__(self, item):
        # The bridge asks for scan_path, comp_path, prep_path... one per track.
        if item.endswith("_path"):
            paths = self.__dict__.get("paths") or {}
            return paths.get(item[:-5])
        raise AttributeError(item)

    def get_frame_count(self) -> int:
        if self.frame_range:
            first, last = self.frame_range
            return max(1, last - first + 1)
        return FALLBACK_DURATION

    @property
    def layers(self) -> List[str]:
        """Which layers this shot actually fills, in track order."""
        return [key for _label, key in TRACK_LAYOUT if self.paths.get(key)]


def _sort_key(shot) -> tuple:
    """
    Shots in the order they cut together.

    Shot names carry their edit order as a number (SH010, SH020), so sorting on
    that number puts the lineup in edit order. Anything unnumbered falls to the
    end alphabetically rather than being dropped.
    """
    name = str(getattr(shot, "shot_name", "") or "")
    numbers = _TRAILING_NUMBER.findall(name)
    if numbers:
        return (0, int(numbers[-1]), name.lower())
    return (1, 0, name.lower())


def _frame_range(shot, clip: Optional[MediaClip]) -> Optional[Tuple[int, int]]:
    """
    How long this shot runs.

    The plate on disk is the truth: it has real first and last frames. The
    dashboard's edit_frames is a count typed by a person and is used only when
    there are no frames to count.
    """
    if clip is not None and clip.is_sequence and clip.frame_count:
        return (clip.first_frame, clip.last_frame)

    # What the ingest recorded when the plate landed. Used when the clip is a
    # movie rather than a sequence, so there are no frame numbers to read.
    first = int(getattr(shot, "first_frame", 0) or 0)
    last = int(getattr(shot, "last_frame", 0) or 0)
    if last and last >= first:
        return (first, last)

    try:
        counted = int(float(getattr(shot, "edit_frames", 0) or 0))
    except (TypeError, ValueError):
        counted = 0

    if counted > 0:
        return (1, counted)
    return None


def build_lineup_shot(shot, project_root=None, folder_resolver=None
                      ) -> Optional[LineupShot]:
    """
    One dashboard shot as a timeline entry, or None if it has no plate yet.

    A shot with no plate is not put on the timeline: the lineup is the edit,
    and a gap where a shot should be is more use than a clip pointing at
    nothing.
    """
    media = available_media(shot, project_root, folder_resolver=folder_resolver)
    scan = media.get("scan")
    if scan is None:
        return None

    paths: Dict[str, Path] = {}
    for _label, key in TRACK_LAYOUT:
        clip = media.get(key)
        if clip is not None:
            paths[key] = clip.path

    return LineupShot(
        name=str(getattr(shot, "shot_name", "") or "shot"),
        reel=str(getattr(shot, "reel_episode", "") or ""),
        fps=float(getattr(shot, "fps", 24.0) or 24.0),
        frame_range=_frame_range(shot, scan),
        paths=paths,
    )


def build_lineup(shots, project_root=None, folder_resolver=None
                 ) -> List[LineupShot]:
    """Every shot that has a plate, in edit order."""
    lineup = []
    for shot in sorted(shots or [], key=_sort_key):
        entry = build_lineup_shot(shot, project_root, folder_resolver)
        if entry is not None:
            lineup.append(entry)
    return lineup


def group_by_reel(lineup: List[LineupShot]) -> Dict[str, List[LineupShot]]:
    """The lineup split into reels, keeping each reel's edit order."""
    reels: Dict[str, List[LineupShot]] = {}
    for entry in lineup:
        reels.setdefault(entry.reel or "Unsorted", []).append(entry)
    return reels


def _safe_name(name: str) -> str:
    clean = "".join(ch if (ch.isalnum() or ch in "_-") else "_"
                    for ch in str(name or "").strip())
    return clean.strip("._ ") or "Lineup"


@dataclass
class LineupResult:
    """What was written, and what could not be."""

    combined: Optional[Path] = None
    per_reel: Dict[str, Path] = field(default_factory=dict)
    shot_count: int = 0
    skipped: List[str] = field(default_factory=list)
    error: str = ""

    @property
    def ok(self) -> bool:
        return bool(self.combined or self.per_reel)

    def summary(self) -> str:
        if self.error:
            return self.error
        if not self.ok:
            return "Nothing to build: no shot has a scan on disk yet."

        parts = [f"{self.shot_count} shot(s)"]
        if self.per_reel:
            parts.append(f"{len(self.per_reel)} reel timeline(s)")
        if self.combined:
            parts.append("one combined timeline")
        if self.skipped:
            parts.append(f"{len(self.skipped)} shot(s) skipped (no scan)")
        return ", ".join(parts)


def generate_timelines(shots, output_dir, project_name="Lineup",
                       project_root=None, folder_resolver=None,
                       prefer_proxy_media: bool = True,
                       bridge=None) -> LineupResult:
    """
    Write a timeline per reel and one combined timeline.

    Returns what was written rather than raising: a lineup that cannot be built
    is something to tell a coordinator about, not a crash.
    """
    result = LineupResult()

    all_shots = list(shots or [])
    lineup = build_lineup(all_shots, project_root, folder_resolver)
    result.shot_count = len(lineup)
    placed = {entry.name for entry in lineup}
    result.skipped = [
        str(getattr(shot, "shot_name", "") or "")
        for shot in all_shots
        if str(getattr(shot, "shot_name", "") or "") not in placed
    ]

    if not lineup:
        return result

    bridge = bridge or OliveBridge()
    output_dir = Path(output_dir)

    try:
        output_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        result.error = f"Could not write to {output_dir}: {exc}"
        return result

    for reel, entries in group_by_reel(lineup).items():
        path = output_dir / f"{_safe_name(project_name)}_{_safe_name(reel)}.ovexml"
        if bridge.generate_project(entries, path,
                                   prefer_proxy_media=prefer_proxy_media,
                                   tracks=TRACK_LAYOUT):
            result.per_reel[reel] = path
        else:
            logger.warning("Could not build the timeline for reel %s", reel)

    combined = output_dir / f"{_safe_name(project_name)}_All_Reels.ovexml"
    if bridge.generate_project(lineup, combined,
                               prefer_proxy_media=prefer_proxy_media,
                               tracks=TRACK_LAYOUT):
        result.combined = combined
    else:
        logger.warning("Could not build the combined timeline")

    return result


def department_labels(keys) -> str:
    """The layers a shot fills, written out for a person."""
    names = {key: label for label, key in TRACK_LAYOUT}
    filled = [names.get(key, key.title()) for key in (keys or [])]
    return " + ".join(filled) if filled else "-"
