"""
Building the lineup - for Olive and for the Timeline Viewer's own player - out
of what the dashboard knows.

The lineup starts as the plates, in reel order: that is the edit, and it exists
before any work is done. As renders arrive they are laid on their own layer
directly beneath the plate they came from, so scrubbing down a column shows the
same moment of the same shot at each stage.

    layer 1   Scan     the plate, always present
    layer 2+  every department in departments.json, in its "order"

The layers used to be a fixed Scan / Comp / Prep / Deage, so DMP, CG, Roto,
Matchmove and Slapcomp renders never appeared (MED-085). A timeline only gets
the layers its shots actually fill.

Shots are ordered by reel, then by every number in the name compared as a
number (SEQ010_SH020 before SEQ020_SH010), so reels are not interleaved
(MED-079).

One project produces a timeline per reel and one holding every reel, because
both get used: a reel to review a section, the combined one to see the show.
They are written into the project, under editorial/lineups (MED-093).
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


def track_layout() -> Tuple[Tuple[str, str], ...]:
    """Scan first, then every department by its order (label, key)."""
    layout = [("Scan", "scan")]
    try:
        from slate.core.domain.departments import load_departments
        for dept in sorted(load_departments(), key=lambda d: (d.order, d.key)):
            if dept.key != "scan":
                layout.append((dept.label or dept.name or dept.key.title(), dept.key))
    except Exception as exc:
        logger.warning("Departments could not be read; the lineup has the plate only: %s", exc)
    return tuple(layout)


# Which department goes on which layer, top down. The plate is always layer 1.
TRACK_LAYOUT: Tuple[Tuple[str, str], ...] = track_layout()

# When a shot has no frame range yet, a clip still needs a length. This is only
# a placeholder; the list shows it as unknown, and the real range replaces it
# as soon as the ingest has recorded one (MED-089).
FALLBACK_DURATION = 100

_NUMBERS = re.compile(r"(\d+)")


def plural(count: int, singular: str, plural_form: str = "") -> str:
    """'1 shot', '2 shots', '1 proxy', '3 proxies', '1,200 shots'."""
    return f"{count:,} {singular if count == 1 else (plural_form or singular + 's')}"


def natural_key(text: str) -> tuple:
    """'SEQ010_SH020' -> ('seq', 10, '_sh', 20): numbers compare as numbers."""
    parts = _NUMBERS.split(str(text or "").lower())
    return tuple(int(p) if p.isdigit() else p for p in parts)


@dataclass
class LineupShot:
    """One shot as Olive needs to see it: a name, a length, and its layers."""

    name: str
    reel: str = ""
    fps: float = 24.0
    frame_range: Optional[Tuple[int, int]] = None
    paths: Dict[str, Path] = field(default_factory=dict)
    clips: Dict[str, MediaClip] = field(default_factory=dict)
    scan_version: str = ""

    def __getattr__(self, item):
        # The bridge asks for scan_path, comp_path, prep_path... one per track.
        if item.endswith("_path"):
            paths = self.__dict__.get("paths") or {}
            return paths.get(item[:-5])
        raise AttributeError(item)

    @property
    def length_known(self) -> bool:
        return self.frame_range is not None

    def get_frame_count(self) -> int:
        if self.frame_range:
            first, last = self.frame_range
            return max(1, last - first + 1)
        return FALLBACK_DURATION

    def frames_text(self) -> str:
        """'48' or '~100 (length unknown)'."""
        if self.length_known:
            return f"{self.get_frame_count()}"
        return f"~{FALLBACK_DURATION} (length unknown)"

    @property
    def layers(self) -> List[str]:
        """Which layers this shot actually fills, in track order."""
        return [key for _label, key in TRACK_LAYOUT if self.paths.get(key)]


def _sort_key(shot) -> tuple:
    """Reel in natural order, then the shot name with every number as a number."""
    reel = str(getattr(shot, "reel_episode", "") or getattr(shot, "reel", "") or "")
    name = str(getattr(shot, "shot_name", "") or getattr(shot, "name", "") or "")
    has_number = bool(_NUMBERS.search(name))
    return (natural_key(reel), 0 if has_number else 1, natural_key(name))


def _frame_range(shot, clip: Optional[MediaClip]) -> Optional[Tuple[int, int]]:
    """
    How long this shot runs.

    The plate on disk is the truth: it has real first and last frames. The
    dashboard's edit_frames is a count typed by a person and is used only when
    there are no frames to count.
    """
    if clip is not None and clip.is_sequence and clip.frame_count:
        return (clip.first_frame, clip.last_frame)

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
    """
    media = available_media(shot, project_root, folder_resolver=folder_resolver)
    scan = media.get("scan")
    if scan is None:
        return None

    paths: Dict[str, Path] = {}
    clips: Dict[str, MediaClip] = {}
    for _label, key in TRACK_LAYOUT:
        clip = media.get(key)
        if clip is not None:
            paths[key] = clip.path
            clips[key] = clip

    return LineupShot(
        name=str(getattr(shot, "shot_name", "") or "shot"),
        reel=str(getattr(shot, "reel_episode", "") or ""),
        fps=float(getattr(shot, "fps", 24.0) or 24.0),
        frame_range=_frame_range(shot, scan),
        paths=paths,
        clips=clips,
        scan_version=getattr(scan, "scan_version", "") or "",
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


@dataclass
class LineupRow:
    """A row of the Timeline Viewer: a shot, and its entry when it has a plate."""
    name: str
    reel: str
    shot: object
    entry: Optional[LineupShot] = None

    @property
    def has_scan(self) -> bool:
        return self.entry is not None


def lineup_rows(shots, project_root=None, folder_resolver=None) -> List[LineupRow]:
    """
    Every shot, in edit order - including the ones with no scan yet, so the
    list can show them greyed out instead of hiding them (MED-087).
    """
    rows = []
    for shot in sorted(shots or [], key=_sort_key):
        rows.append(LineupRow(
            name=str(getattr(shot, "shot_name", "") or "shot"),
            reel=str(getattr(shot, "reel_episode", "") or ""),
            shot=shot,
            entry=build_lineup_shot(shot, project_root, folder_resolver),
        ))
    return rows


def lineup_fps(lineup: List[LineupShot]) -> float:
    """The lineup's rate: the most common shot rate (24 when there are none)."""
    counts: Dict[float, int] = {}
    for entry in lineup or []:
        counts[round(float(entry.fps or 24.0), 3)] = counts.get(round(float(entry.fps or 24.0), 3), 0) + 1
    if not counts:
        return 24.0
    return max(counts.items(), key=lambda kv: (kv[1], -abs(kv[0] - 24.0)))[0]


def fps_mismatches(lineup: List[LineupShot]) -> List[str]:
    """Shots whose rate differs from the lineup's (MED-089)."""
    rate = lineup_fps(lineup)
    return [e.name for e in lineup or [] if abs(float(e.fps or 24.0) - rate) > 0.01]


def group_by_reel(lineup: List[LineupShot]) -> Dict[str, List[LineupShot]]:
    """The lineup split into reels, keeping each reel's edit order."""
    reels: Dict[str, List[LineupShot]] = {}
    for entry in lineup:
        reels.setdefault(entry.reel or "Unsorted", []).append(entry)
    return reels


def layout_for(lineup: List[LineupShot]) -> Tuple[Tuple[str, str], ...]:
    """The layers this lineup fills: the plate always, other departments when used."""
    used = {key for entry in lineup for key in entry.layers}
    return tuple((label, key) for label, key in TRACK_LAYOUT if key == "scan" or key in used)


def _safe_name(name: str) -> str:
    clean = "".join(ch if (ch.isalnum() or ch in "_-") else "_"
                    for ch in str(name or "").strip())
    return clean.strip("._ ") or "Lineup"


def lineup_folder(project_root=None, project_name: str = "") -> Path:
    """
    Where a project's timelines are written: <project>/editorial/lineups, so
    every editor finds them. Without a project folder, the studio folder
    (SERVER_ROOT/Lineups/<project>). They used to go to a per-user folder under
    an old product name (MED-093).
    """
    if project_root:
        return Path(project_root) / "editorial" / "lineups"
    from slate.core.infra.global_config import GlobalConfig
    return GlobalConfig.server_root() / "Lineups" / _safe_name(project_name)


@dataclass
class LineupResult:
    """What was written, and what could not be."""

    combined: Optional[Path] = None
    per_reel: Dict[str, Path] = field(default_factory=dict)
    shot_count: int = 0
    skipped: List[str] = field(default_factory=list)
    fps_mismatch: List[str] = field(default_factory=list)
    error: str = ""

    @property
    def ok(self) -> bool:
        return bool(self.combined or self.per_reel)

    def summary(self) -> str:
        if self.error:
            return self.error
        if not self.ok:
            if not self.shot_count and not self.skipped:
                return "No shots loaded - refresh from the dashboard first."
            return "Nothing to build: no shot has a scan on disk yet."

        parts = [plural(self.shot_count, "shot")]
        if self.per_reel:
            parts.append(plural(len(self.per_reel), "reel timeline"))
        if self.combined:
            parts.append("one combined timeline")
        if self.skipped:
            parts.append(f"{plural(len(self.skipped), 'shot')} skipped (no scan)")
        if self.fps_mismatch:
            parts.append(f"{plural(len(self.fps_mismatch), 'shot')} at a different frame rate")
        return ", ".join(parts)


def generate_timelines(shots, output_dir, project_name="Lineup",
                       project_root=None, folder_resolver=None,
                       prefer_proxy_media: bool = True,
                       bridge=None, lineup: List[LineupShot] = None) -> LineupResult:
    """
    Write a timeline per reel and one combined timeline.

    lineup, when given, is the list to write (the Timeline Viewer passes the
    shots left ticked); otherwise it is built from shots. Returns what was
    written rather than raising.
    """
    result = LineupResult()

    all_shots = list(shots or [])
    if lineup is None:
        lineup = build_lineup(all_shots, project_root, folder_resolver)
    result.shot_count = len(lineup)
    with_scan = {entry.name for entry in build_lineup(all_shots, project_root, folder_resolver)} \
        if all_shots else set()
    result.skipped = [
        str(getattr(shot, "shot_name", "") or "")
        for shot in all_shots
        if str(getattr(shot, "shot_name", "") or "") not in with_scan
    ]
    result.fps_mismatch = fps_mismatches(lineup)

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
                                   tracks=layout_for(entries)):
            result.per_reel[reel] = path
        else:
            logger.warning("Could not build the timeline for reel %s", reel)

    combined = output_dir / f"{_safe_name(project_name)}_All_Reels.ovexml"
    if bridge.generate_project(lineup, combined,
                               prefer_proxy_media=prefer_proxy_media,
                               tracks=layout_for(lineup)):
        result.combined = combined
    else:
        logger.warning("Could not build the combined timeline")

    return result


def department_labels(keys) -> str:
    """The layers a shot fills, written out for a person."""
    names = {key: label for label, key in TRACK_LAYOUT}
    filled = [names.get(key, key.title()) for key in (keys or [])]
    return " + ".join(filled) if filled else "—"
