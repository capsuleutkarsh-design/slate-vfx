"""
Building the lineup - for the Timeline Viewer's player, RV and the editors'
EDLs - out of what the dashboard knows.

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

One project produces an EDL per reel and one holding every reel, because both
get used: a reel to review a section, the combined one to see the show. They
are written into the project, under editorial/lineups (MED-093). They replace
the Olive timelines: Olive is no longer developed, and every editor's tool
reads a CMX 3600 EDL.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

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
    """One shot of the lineup: a name, a length, and its layers."""

    name: str
    reel: str = ""
    fps: float = 24.0
    frame_range: Optional[Tuple[int, int]] = None
    paths: Dict[str, Path] = field(default_factory=dict)
    clips: Dict[str, MediaClip] = field(default_factory=dict)
    scan_version: str = ""

    def clip_for(self, layer: str) -> MediaClip:
        """The layer's render, or the plate while the shot has none yet."""
        return self.clips.get(layer) or self.clips["scan"]

    @property
    def length_known(self) -> bool:
        return self.frame_range is not None

    def get_frame_count(self) -> int:
        if self.frame_range:
            first, last = self.frame_range
            return max(1, last - first + 1)
        return FALLBACK_DURATION

    def frames_text(self) -> str:
        """'48' or 'length unknown' - never the placeholder number (MED2-046)."""
        if self.length_known:
            return f"{self.get_frame_count()}"
        return "length unknown"

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


# A sequence has no rate of its own: it plays at its project's rate (Edit
# project > Frame rate), 24 until a project says otherwise. Movies keep their own.
SEQUENCE_FPS = 24.0


def project_fps(project) -> float:
    """The rate a project's image sequences play at (ProjectConfig.fps, or its stored dict)."""
    value = project.get("fps") if isinstance(project, dict) else getattr(project, "fps", 0)
    try:
        fps = float(value or 0)
    except (TypeError, ValueError):
        fps = 0.0
    return fps if fps > 0 else SEQUENCE_FPS


def plate_facts(clip: Optional[MediaClip], sequence_fps: float = SEQUENCE_FPS) -> Tuple[float, int]:
    """
    (rate, frame count) read from a movie plate - (sequence_fps, 0) for a
    sequence or when the file cannot be read. Every shot used to be listed
    and written at 24 because the rate was asked of a dashboard field that
    does not exist (MED2-041), and a movie plate's length was never read
    (MED2-042). Runs on the Timeline's scan thread.
    """
    if clip is None or clip.is_sequence:
        return sequence_fps, 0
    try:
        from slate.core.domain.metadata_engine import SmartMetadataManager
        meta = SmartMetadataManager.extract_tech_metadata(str(clip.path)) or {}
        fps = float(meta.get("fps") or 0)
        duration = float(meta.get("duration_sec") or 0)
    except Exception as exc:
        logger.debug("Plate not probed (%s): %s", clip.path, exc)
        return sequence_fps, 0
    if fps <= 0:
        return sequence_fps, 0
    return fps, int(round(duration * fps))


def _frame_range(shot, clip: Optional[MediaClip], movie_frames: int = 0
                 ) -> Optional[Tuple[int, int]]:
    """
    How long this shot runs.

    The plate on disk is the truth: a sequence's real first and last frames,
    or a movie's own length. The dashboard's edit_frames is a count typed by
    a person and is used only when there are no frames to count.
    """
    if clip is not None and clip.is_sequence and clip.frame_count:
        return (clip.first_frame, clip.last_frame)

    first = int(getattr(shot, "first_frame", 0) or 0)
    last = int(getattr(shot, "last_frame", 0) or 0)
    if last and last >= first:
        return (first, last)

    if movie_frames > 0:
        return (1, movie_frames)

    try:
        counted = int(float(getattr(shot, "edit_frames", 0) or 0))
    except (TypeError, ValueError):
        counted = 0

    if counted > 0:
        return (1, counted)
    return None


def build_lineup_shot(shot, project_root=None, folder_resolver=None,
                      sequence_fps: float = SEQUENCE_FPS) -> Optional[LineupShot]:
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

    fps, movie_frames = plate_facts(scan, sequence_fps)
    return LineupShot(
        name=str(getattr(shot, "shot_name", "") or "shot"),
        reel=str(getattr(shot, "reel_episode", "") or ""),
        fps=fps,
        frame_range=_frame_range(shot, scan, movie_frames),
        paths=paths,
        clips=clips,
        scan_version=getattr(scan, "scan_version", "") or "",
    )


def build_lineup(shots, project_root=None, folder_resolver=None,
                 sequence_fps: float = SEQUENCE_FPS) -> List[LineupShot]:
    """Every shot that has a plate, in edit order."""
    lineup = []
    for shot in sorted(shots or [], key=_sort_key):
        entry = build_lineup_shot(shot, project_root, folder_resolver, sequence_fps)
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


def lineup_rows(shots, project_root=None, folder_resolver=None,
                sequence_fps: float = SEQUENCE_FPS) -> List[LineupRow]:
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
            entry=build_lineup_shot(shot, project_root, folder_resolver, sequence_fps),
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


# Editorial convention: a timeline's first frame is at one hour.
RECORD_START_SECONDS = 3600


def timecode(frame: int, rate: int) -> str:
    """A frame count as non-drop SMPTE timecode, HH:MM:SS:FF."""
    seconds, ff = divmod(int(frame), rate)
    minutes, ss = divmod(seconds, 60)
    hh, mm = divmod(minutes, 60)
    return f"{hh:02d}:{mm:02d}:{ss:02d}:{ff:02d}"


def edl_text(entries: List[LineupShot], title: str, layer: str = "scan") -> str:
    """
    A CMX 3600 EDL of these shots in order, for Resolve, Premiere or Avid.

    Each event is the layer's render, or the plate while the shot has none
    yet, at the length of the plate. A sequence keeps its own frame numbers
    as source timecode; a movie starts at zero. The clip and file are written
    as the comments editors relink by. A rate like 23.976 is written on a
    24 timebase, non-drop.
    """
    rate = max(1, round(lineup_fps(entries)))
    record = RECORD_START_SECONDS * rate
    lines = [f"TITLE: {title}", "FCM: NON-DROP FRAME", ""]
    for number, entry in enumerate(entries, start=1):
        clip = entry.clip_for(layer)
        length = entry.get_frame_count()
        source = clip.first_frame if clip.is_sequence else 0
        lines += [
            f"{number:03d}  AX       V     C        "
            f"{timecode(source, rate)} {timecode(source + length, rate)} "
            f"{timecode(record, rate)} {timecode(record + length, rate)}",
            f"* FROM CLIP NAME: {clip.path.name}",
            f"* SOURCE FILE: {clip.path}",
            "",
        ]
        record += length
    return "\n".join(lines)


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
            parts.append(plural(len(self.per_reel), "reel EDL"))
        if self.combined:
            parts.append("one EDL of every reel")
        if self.skipped:
            parts.append(f"{plural(len(self.skipped), 'shot')} skipped (no scan)")
        if self.fps_mismatch:
            parts.append(f"{plural(len(self.fps_mismatch), 'shot')} at a different frame rate")
        return ", ".join(parts)


def combined_edl_name(project_name: str, layer: str) -> str:
    return f"{_safe_name(project_name)}_All_Reels_{_safe_name(layer)}.edl"


def generate_timelines(shots, output_dir, project_name="Lineup",
                       project_root=None, folder_resolver=None,
                       layer: str = "scan", lineup: List[LineupShot] = None,
                       sequence_fps: float = SEQUENCE_FPS) -> LineupResult:
    """
    Write an EDL per reel and one of every reel, of the chosen layer.

    lineup, when given, is the list to write (the Timeline Viewer passes the
    shots left ticked); otherwise it is built from shots. Returns what was
    written rather than raising.
    """
    result = LineupResult()

    all_shots = list(shots or [])
    if lineup is None:
        lineup = build_lineup(all_shots, project_root, folder_resolver, sequence_fps)
    result.shot_count = len(lineup)
    # Which shots have a plate: the folders only - no need to probe every
    # movie again here.
    with_scan = {str(getattr(shot, "shot_name", "") or "") for shot in all_shots
                 if available_media(shot, project_root,
                                    folder_resolver=folder_resolver).get("scan") is not None}
    result.skipped = [
        str(getattr(shot, "shot_name", "") or "")
        for shot in all_shots
        if str(getattr(shot, "shot_name", "") or "") not in with_scan
    ]
    result.fps_mismatch = fps_mismatches(lineup)

    if not lineup:
        return result

    output_dir = Path(output_dir)
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
        for reel, entries in group_by_reel(lineup).items():
            path = output_dir / f"{_safe_name(project_name)}_{_safe_name(reel)}_{_safe_name(layer)}.edl"
            path.write_text(edl_text(entries, f"{project_name} {reel}", layer), encoding="utf-8")
            result.per_reel[reel] = path
        combined = output_dir / combined_edl_name(project_name, layer)
        combined.write_text(edl_text(lineup, f"{project_name} all reels", layer), encoding="utf-8")
        result.combined = combined
    except OSError as exc:
        result.error = f"Could not write to {output_dir}: {exc}"
    return result


def department_labels(keys) -> str:
    """The layers a shot fills, written out for a person."""
    names = {key: label for label, key in TRACK_LAYOUT}
    filled = [names.get(key, key.title()) for key in (keys or [])]
    return " + ".join(filled) if filled else "—"
