"""
Opening a shot in OpenRV.

The dashboard knows which shot a person is looking at; RV knows how to play it.
This is the piece in between: work out what a shot actually has - the plate,
each department's render - and hand the chosen ones to RV.

Picking more than one department loads them as a playlist, so a comp can be
compared against the plate it was built from in the same session.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from slate.core.domain.shot_media import (
    MediaClip, available_media, department_label,
)


logger = logging.getLogger(__name__)

_VERSION = re.compile(r"(?<![A-Za-z0-9])([vV]\d{2,4})(?![0-9])")

# The plate first, then the departments that most often get reviewed against
# it. Anything not named here follows in registry order.
PREFERRED_ORDER = ["scan", "comp", "slapcomp", "prep", "deage", "roto",
                   "matchmove", "cg", "dmp", "ai", "mgfx"]


@dataclass
class ReviewOption:
    """One department a person can choose to look at."""

    key: str
    label: str
    clip: MediaClip

    @property
    def version(self) -> str:
        """
        The scan's version, or the one a render carries in its name or
        folder ('SEQ010_SH020_comp_v002.mov' -> 'v002'): renders showed a dash
        (MED2-062).
        """
        if self.clip.scan_version:
            return self.clip.scan_version
        for part in (self.clip.path.name, self.clip.path.parent.name):
            found = _VERSION.findall(part)
            if found:
                return found[-1].lower()
        return ""

    @property
    def frames(self) -> str:
        """'1001-1008 (8 f)' for a sequence, the file name for a movie."""
        if self.clip.is_sequence and self.clip.frame_count:
            return (f"{self.clip.first_frame}–{self.clip.last_frame} "
                    f"({self.clip.frame_count} f)")
        return self.clip.path.name

    @property
    def detail(self) -> str:
        """
        What would open, the same way for every row: 'v001 · 1001-1008 (8 f)'.

        It showed a printf pattern with the version for the plate and a frame
        count without a version for renders (MED-122).
        """
        parts = [p for p in (self.version, self.frames) if p]
        return " · ".join(parts)


@dataclass
class ReviewRequest:
    """What to open in RV for one shot."""

    shot_name: str = ""
    options: List[ReviewOption] = field(default_factory=list)

    def media_paths(self, keys) -> List[str]:
        wanted = list(keys or [])
        by_key = {opt.key: opt for opt in self.options}
        return [str(by_key[key].clip.path) for key in wanted if key in by_key]


def _order_index(key: str) -> int:
    try:
        return PREFERRED_ORDER.index(key)
    except ValueError:
        return len(PREFERRED_ORDER)


def build_request(shot, project_root=None, folder_resolver=None) -> ReviewRequest:
    """
    What this shot offers for review.

    Only departments with media on disk are included: offering a department
    that has rendered nothing produces a player that opens on a black frame and
    a person who thinks the pipeline is broken.
    """
    media: Dict[str, MediaClip] = available_media(
        shot, project_root, folder_resolver=folder_resolver
    )

    options = [
        ReviewOption(key=key, label=department_label(key), clip=clip)
        for key, clip in media.items()
    ]
    options.sort(key=lambda opt: (_order_index(opt.key), opt.label))

    return ReviewRequest(
        shot_name=str(getattr(shot, "shot_name", "") or getattr(shot, "name", "")),
        options=options,
    )


def launch(paths: List[str], launcher=None) -> bool:
    """
    Send media to RV, as a playlist when there is more than one.

    Returns False rather than raising: a review player that will not start is a
    message to show, not a crash.
    """
    paths = [str(p) for p in (paths or []) if p]
    if not paths:
        return False

    if launcher is None:
        from slate.core.rv_integration import RVLauncher
        launcher = RVLauncher()

    try:
        if len(paths) == 1:
            return bool(launcher.launch_media(paths[0]))
        return bool(launcher.launch_playlist(paths))
    except Exception as exc:
        logger.exception("Could not launch RV: %s", exc)
        return False
