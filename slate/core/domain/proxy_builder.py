"""
Making review proxies for a project's plates and renders.

Olive and RV can both play EXR sequences, but slowly: a reel of 2K EXRs is
gigabytes a shot, and scrubbing a lineup built from them is painful. An MP4
beside each sequence fixes that, and both the Olive bridge and the review
picker already prefer a proxy when one is there.

This runs when someone asks for it - after an ingest has been checked over -
rather than during the ingest itself, so a delivery is never held up behind
ffmpeg.

Proxies are written to a ``proxy`` folder beside the media they came from, which
is exactly where the Olive bridge already looks for them.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional

from slate.core.domain.shot_media import (
    MOVIE_SUFFIXES, MediaClip, available_media, department_label,
)


logger = logging.getLogger(__name__)

# Where a proxy goes, relative to the media it was made from. The Olive bridge
# searches this name already, so a proxy put here is found without any wiring.
PROXY_FOLDER = "proxy"


@dataclass
class ProxyJob:
    """One proxy that needs making."""

    shot_name: str
    department: str
    source: Path
    target: Path
    is_sequence: bool = False

    @property
    def label(self) -> str:
        return f"{self.shot_name} {department_label(self.department)}"


@dataclass
class ProxyBuildResult:
    """What happened, in terms a coordinator can act on."""

    built: List[str] = field(default_factory=list)
    already_there: List[str] = field(default_factory=list)
    failed: List[str] = field(default_factory=list)
    cancelled: bool = False
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.error and not self.failed

    def summary(self) -> str:
        if self.error:
            return self.error

        parts = []
        if self.built:
            parts.append(f"{len(self.built)} proxy(s) made")
        if self.already_there:
            parts.append(f"{len(self.already_there)} already there")
        if self.failed:
            parts.append(f"{len(self.failed)} failed")
        if self.cancelled:
            parts.append("stopped early")

        return ", ".join(parts) if parts else "Nothing needed a proxy."


def first_frame_file(clip: MediaClip) -> Path:
    """
    A real file ffmpeg can be pointed at.

    A sequence is held as a printf pattern, which is not a file. ffmpeg needs
    a frame to start from, so the pattern is filled in with the first frame.
    """
    if not clip.is_sequence:
        return clip.path
    try:
        return Path(str(clip.path) % clip.first_frame)
    except (TypeError, ValueError):
        return clip.path


def proxy_path_for(clip: MediaClip, shot_name: str) -> Path:
    """Where this clip's proxy belongs."""
    department = clip.department or "media"
    name = f"{shot_name}_{department}_proxy.mp4"
    return clip.path.parent / PROXY_FOLDER / name


def needs_proxy(clip: MediaClip) -> bool:
    """
    Whether this clip is worth making a proxy of.

    Something that is already a movie plays fine as it is; making an MP4 of an
    MP4 costs time and quality for nothing.
    """
    return clip.path.suffix.lower() not in MOVIE_SUFFIXES


def plan(shots, project_root=None, folder_resolver=None) -> List[ProxyJob]:
    """Every proxy this project is missing, plate first for each shot."""
    jobs: List[ProxyJob] = []

    for shot in shots or []:
        shot_name = str(getattr(shot, "shot_name", "") or "").strip()
        if not shot_name:
            continue

        media: Dict[str, MediaClip] = available_media(
            shot, project_root, folder_resolver=folder_resolver
        )
        for key, clip in media.items():
            if not needs_proxy(clip):
                continue
            source = first_frame_file(clip)
            if not source.exists():
                continue
            jobs.append(ProxyJob(
                shot_name=shot_name,
                department=key,
                source=source,
                target=proxy_path_for(clip, shot_name),
                is_sequence=clip.is_sequence,
            ))

    return jobs


def build(jobs: List[ProxyJob], manager=None,
          progress: Optional[Callable[[int, int, str], None]] = None,
          should_stop: Optional[Callable[[], bool]] = None,
          overwrite: bool = False) -> ProxyBuildResult:
    """
    Make the proxies.

    ``progress`` is called with (done, total, label) so a person can watch it
    happen; ``should_stop`` is checked between jobs so it can be cancelled
    without killing ffmpeg mid-file and leaving a broken MP4 behind.
    """
    result = ProxyBuildResult()
    jobs = list(jobs or [])

    if not jobs:
        return result

    if manager is None:
        from slate.core.domain.proxy_manager import proxy_manager
        manager = proxy_manager

    if not getattr(manager, "ffmpeg_path", None):
        result.error = (
            "ffmpeg was not found, so no proxies can be made. Install it "
            "alongside the software or put it on PATH."
        )
        return result

    total = len(jobs)
    for index, job in enumerate(jobs, start=1):
        if should_stop is not None and should_stop():
            result.cancelled = True
            break

        if progress is not None:
            progress(index, total, job.label)

        if job.target.exists() and not overwrite:
            result.already_there.append(job.label)
            continue

        try:
            job.target.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            logger.warning("Could not make the proxy folder for %s: %s",
                           job.label, exc)
            result.failed.append(job.label)
            continue

        try:
            ok, _path = manager.generate_proxy(
                input_path=job.source,
                is_seq=job.is_sequence,
                proxy_path=job.target,
            )
        except Exception as exc:
            logger.exception("Proxy failed for %s: %s", job.label, exc)
            ok = False

        if ok and job.target.exists():
            result.built.append(job.label)
        else:
            result.failed.append(job.label)

    return result
