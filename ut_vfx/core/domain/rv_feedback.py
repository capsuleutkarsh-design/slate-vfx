"""
Bringing a verdict back from OpenRV.

A supervisor reviews in RV and marks a shot approved or a retake. RV writes
what happened to a small file; the software watches that file and puts the
verdict on the right shot.

Two things made the old version of this never work. It looked for a tab called
"VFX Dashboard PRO" (the tab is "VFX Dashboard"), and it matched the media
against ``shot.render_path`` and ``shot.scan_path`` - attributes a dashboard
shot has never had. So this module does the matching itself, from the one thing
that is always true: the media sits inside the shot's own folder.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from ut_vfx.core.domain.departments import load_departments


logger = logging.getLogger(__name__)

# What RV calls a verdict, and what the dashboard calls it.
STATUS_MAP = {
    "approved": "Approved",
    "rejected": "Retake",
    "retake": "Retake",
}


@dataclass
class RVFeedback:
    """One verdict handed back by RV."""

    status: str = ""
    media_path: str = ""
    frame: Optional[int] = None
    note: str = ""
    annotation_path: str = ""
    reviewer: str = ""
    timestamp: float = 0.0

    @property
    def dashboard_status(self) -> str:
        return STATUS_MAP.get(str(self.status or "").lower(), "")

    def is_usable(self) -> bool:
        # A note on its own is worth keeping: a supervisor pointing something
        # out mid-review is not always calling for a retake.
        return bool(self.media_path and (self.dashboard_status or self.note))


def read_feedback(path) -> Optional[RVFeedback]:
    """Read the file RV wrote, or None if there is nothing usable in it."""
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError) as exc:
        logger.debug("Could not read RV feedback at %s: %s", path, exc)
        return None

    if not isinstance(data, dict):
        return None

    frame = data.get("frame")
    try:
        frame = int(frame) if frame is not None else None
    except (TypeError, ValueError):
        frame = None

    feedback = RVFeedback(
        status=str(data.get("status") or ""),
        media_path=str(data.get("media_path") or ""),
        frame=frame,
        note=str(data.get("note") or ""),
        annotation_path=str(data.get("annotation_path") or ""),
        reviewer=str(data.get("reviewer") or ""),
        timestamp=float(data.get("timestamp") or 0.0),
    )
    return feedback if feedback.is_usable() else None


def _parts(media_path: str) -> List[str]:
    """The folder names in a path, lowercased."""
    try:
        return [part.lower() for part in Path(str(media_path)).parts]
    except (TypeError, ValueError):
        return []


def department_from_path(media_path: str) -> str:
    """
    Which department's folder this media came out of.

    Matched on the whole folder name from the registry (``07_Comp``), so a note
    lands on comp rather than on whatever department happens to share a
    substring with the path.
    """
    parts = set(_parts(media_path))
    if "01_scan" in parts:
        return "scan"
    for dept in load_departments():
        if dept.folder and dept.folder.lower() in parts:
            return dept.key
    return ""


def match_shot(shots, media_path: str):
    """
    The shot this media belongs to, or None.

    Matching is on whole path components, never on substrings: SH010 and
    SH0100 are different shots, and a substring match would put a supervisor's
    verdict on the wrong one. When two reels hold a shot of the same name, the
    reel in the path decides.
    """
    parts = _parts(media_path)
    if not parts:
        return None

    part_set = set(parts)
    candidates = []
    for shot in shots or []:
        name = str(getattr(shot, "shot_name", "") or "").strip().lower()
        if name and name in part_set:
            candidates.append(shot)

    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0]

    for shot in candidates:
        reel = str(getattr(shot, "reel_episode", "") or "").strip().lower()
        if reel and reel in part_set:
            return shot

    # Same shot name in two reels and no reel in the path: refuse to guess.
    logger.warning(
        "RV feedback for %s matches %d shots; not guessing which.",
        media_path, len(candidates),
    )
    return None


def note_text(feedback: RVFeedback) -> str:
    """The note as a person would write it, with the frame it was made on."""
    pieces = [f"RV: {feedback.dashboard_status or 'Note'}"]
    if feedback.frame is not None:
        pieces.append(f"frame {feedback.frame}")

    head = " - ".join(pieces)
    body = feedback.note.strip()
    line = f"{head}. {body}" if body else f"{head}."

    if feedback.annotation_path:
        # The full path, so an artist can actually open it. A bare filename
        # tells them a picture exists somewhere and nothing more.
        line += f"{chr(10)}Annotation: {feedback.annotation_path}"
    return line


def file_annotation(shot, feedback: RVFeedback, project_root=None,
                    folder_resolver=None) -> str:
    """
    Move the annotated frame into the shot's own annotation folder.

    RV writes the image to the reviewer's machine. Left there, an artist reads
    "see the annotation" and has nothing to open - the picture is on somebody
    else's laptop. Filed with the shot, it is where anyone looking at that shot
    would think to look.

    Returns where it ended up, or "" if it could not be filed.
    """
    source = feedback.annotation_path
    if not source:
        return ""

    source_path = Path(source)
    if not source_path.is_file():
        logger.debug("Annotation image is gone: %s", source)
        return ""

    from ut_vfx.core.domain.shot_media import annotation_folder

    folder = annotation_folder(shot, project_root, folder_resolver)
    if folder is None:
        return ""

    try:
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / source_path.name

        # Two notes on the same frame in the same second would collide.
        counter = 1
        while target.exists() and target.stat().st_size != source_path.stat().st_size:
            target = folder / f"{source_path.stem}_{counter}{source_path.suffix}"
            counter += 1

        if not target.exists():
            shutil.copy2(source_path, target)
        return str(target)
    except OSError as exc:
        logger.warning("Could not file the annotation for %s: %s",
                       getattr(shot, "shot_name", "?"), exc)
        return ""


def apply_feedback(shot, feedback: RVFeedback, project_root=None,
                   folder_resolver=None) -> bool:
    """
    Put the verdict on the shot.

    The status goes on the department the media came from as well as on the
    shot, because a retake on the comp is a comp retake - leaving the
    department alone is how a shot ends up marked for a retake nobody is
    assigned to.
    """
    # File the picture before writing the note, so the note points at where it
    # actually ended up rather than at the reviewer's own machine.
    filed = file_annotation(shot, feedback, project_root, folder_resolver)
    if filed:
        feedback.annotation_path = filed

    status = feedback.dashboard_status

    if status:
        shot.status = status

        department = department_from_path(feedback.media_path)
        if department and department != "scan" and hasattr(shot, "dept"):
            try:
                shot.dept(department).status = status
            except Exception as exc:
                logger.debug("Could not set %s status: %s", department, exc)
    elif not feedback.note:
        return False

    _append_note(shot, feedback)
    return True


def _append_note(shot, feedback: RVFeedback) -> None:
    """Record the note where the shot's other feedback lives."""
    try:
        from ut_vfx.gui.tabs.vfx_dashboard_pro.models.shot_model import FeedbackEntry
    except Exception as exc:  # pragma: no cover - only when the model moves
        logger.debug("Feedback model unavailable: %s", exc)
        return

    entry = FeedbackEntry(
        date=datetime.now().strftime("%Y-%m-%d %H:%M"),
        source="OpenRV",
        text=note_text(feedback),
        logged_by=feedback.reviewer or "review",
    )

    existing = getattr(shot, "feedback_internal", None)
    if isinstance(existing, list):
        existing.append(entry)
    else:
        shot.feedback_internal = [entry]
