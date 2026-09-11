"""
Spotting a stitch: one shot delivered as two or more scans.

A stitch arrives as folders that look almost the same - SH010_A and SH010_B,
SH010_left and SH010_right, SH010_pt1 and SH010_pt2. They are one shot with one
comp, one bid and one delivery, but the ingest used to make two of everything.

The suffix is not fixed between projects, so this does not look for a list of
known endings. It groups folders that share a shot-like prefix and differ only
by a short tail, and then **asks** before merging anything: guessing wrong here
would fuse two genuinely separate shots, which is worse than doing nothing.

Version suffixes (_ScanA, _v02, _Rescan) are handled earlier by the ingest's
own normalisation and arrive here already merged, so what reaches this module
is genuinely distinct shot names.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Tuple


# What separates one token from the next in a folder name.
_SEPARATORS = re.compile(r"[_\-.\s]+")

# A tail longer than this is probably a real name, not a part marker.
MAX_TAIL_LENGTH = 8

# A prefix must look like a shot: shot names carry a number.
_HAS_DIGIT = re.compile(r"\d")


def _tokens(name: str) -> List[str]:
    return [t for t in _SEPARATORS.split(str(name or "").strip()) if t]


def _common_prefix(a: List[str], b: List[str]) -> List[str]:
    shared = []
    for left, right in zip(a, b):
        if left.lower() != right.lower():
            break
        shared.append(left)
    return shared


@dataclass
class StitchGroup:
    """Folders that look like parts of one shot."""
    shot_name: str
    parts: List[str] = field(default_factory=list)
    reel: str = ""

    @property
    def part_labels(self) -> List[str]:
        """The differing tail of each part, for showing to a person."""
        prefix = _tokens(self.shot_name)
        labels = []
        for part in self.parts:
            tail = _tokens(part)[len(prefix):]
            labels.append("_".join(tail) if tail else "(whole)")
        return labels

    def describe(self) -> str:
        return f"{' + '.join(self.parts)}  ->  {self.shot_name}"


def _pair_is_stitch(a: str, b: str) -> Optional[str]:
    """
    The shot name these two are parts of, or None if they are separate shots.

    The rules are deliberately narrow:
      * they share a leading run of whole tokens
      * that shared prefix contains a digit, so it looks like a shot name
      * each name is the prefix plus at most one extra token
      * the extra tokens differ, and are short

    SH010_A / SH010_B      -> SH010      (parts)
    SH010   / SH011        -> None       (different shots; no shared token)
    SH010   / SH010_A      -> SH010      (whole plus a part)
    SH010_bg / SH010_fg    -> SH010      (parts)
    """
    tokens_a, tokens_b = _tokens(a), _tokens(b)
    if not tokens_a or not tokens_b:
        return None

    prefix = _common_prefix(tokens_a, tokens_b)
    if not prefix:
        return None

    # The shared part has to look like a shot identifier, otherwise names such
    # as "plate_one" and "plate_two" would fuse.
    if not _HAS_DIGIT.search("".join(prefix)):
        return None

    tail_a = tokens_a[len(prefix):]
    tail_b = tokens_b[len(prefix):]

    # One extra token each at most: SH010_A_v2 vs SH010_B is not a safe guess.
    if len(tail_a) > 1 or len(tail_b) > 1:
        return None

    # Identical names are not a stitch.
    if not tail_a and not tail_b:
        return None

    for tail in (tail_a, tail_b):
        if tail and len(tail[0]) > MAX_TAIL_LENGTH:
            return None

    if [t.lower() for t in tail_a] == [t.lower() for t in tail_b]:
        return None

    return "_".join(prefix)


def find_stitch_groups(folder_names: Iterable[str],
                       reel: str = "") -> List[StitchGroup]:
    """
    Group folder names that look like parts of one shot.

    Only groups of two or more are returned; a folder that pairs with nothing
    is left alone.
    """
    # De-duplicate but keep the order. A folder listing cannot really contain
    # the same name twice, and if a caller passes one it is still one folder.
    names, seen = [], set()
    for raw in folder_names:
        name = str(raw).strip()
        if name and name.lower() not in seen:
            seen.add(name.lower())
            names.append(name)

    if len(names) < 2:
        return []

    # Union-find over the pairs that look like parts of the same shot.
    parent: Dict[str, str] = {n: n for n in names}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(x: str, y: str) -> None:
        rx, ry = find(x), find(y)
        if rx != ry:
            parent[ry] = rx

    shot_names: Dict[str, str] = {}
    for i, left in enumerate(names):
        for right in names[i + 1:]:
            shot = _pair_is_stitch(left, right)
            if shot:
                union(left, right)
                # Keep the shortest shot name proposed for this cluster.
                root = find(left)
                current = shot_names.get(root)
                if current is None or len(shot) < len(current):
                    shot_names[root] = shot

    clusters: Dict[str, List[str]] = {}
    for name in names:
        clusters.setdefault(find(name), []).append(name)

    groups = []
    for root, parts in clusters.items():
        if len(parts) < 2:
            continue
        shot = shot_names.get(root) or min(parts, key=len)
        groups.append(StitchGroup(shot_name=shot, parts=sorted(parts), reel=reel))

    groups.sort(key=lambda g: g.shot_name)
    return groups


def group_by_reel(shots_by_reel: Dict[str, Iterable[str]]) -> List[StitchGroup]:
    """
    Find stitch groups within each reel.

    Grouping never crosses a reel: SH010 in ReelA and SH010 in ReelB are
    different shots, which is the whole point of the reel being part of a
    shot's identity.
    """
    groups: List[StitchGroup] = []
    for reel, names in (shots_by_reel or {}).items():
        groups.extend(find_stitch_groups(names, reel=reel))
    return groups


def apply_groups(collected: List[Tuple[object, str]],
                 groups: List[StitchGroup]) -> Dict[object, str]:
    """
    Build the lookup the ingest uses: {folder -> merged shot name}.

    The ingest uses this to send every part of a stitch into one shot folder,
    in one scan version, side by side - which is what a stitch delivered as a
    single folder already produces.

    A group that knows its reel is keyed by ``(reel, folder)``: accepting a
    stitch in one reel must not silently merge the same-looking folders in
    another reel the coordinator rejected.
    """
    mapping: Dict[object, str] = {}
    for group in groups:
        for part in group.parts:
            key = (group.reel, part) if group.reel else part
            mapping[key] = group.shot_name
    return mapping


def survey_source(source_path, target_reel_name: str = "") -> List[StitchGroup]:
    """
    Look at a client drive and report the stitches it appears to contain.

    This walks the source exactly the way the ingest will - same shot
    detection, same reel grouping - so what a coordinator is asked to confirm
    is what would actually be moved.
    """
    # Imported here: the ingest worker pulls in Qt, and this module is used by
    # tests and tools that have no GUI.
    from slate.core.workers.structure import (
        collect_shot_folders, group_shots_by_reel,
    )

    shots = collect_shot_folders(source_path)
    if not shots:
        return []

    by_reel = group_shots_by_reel(shots, source_path, target_reel_name)
    return group_by_reel({
        reel: [path.name for path in paths] for reel, paths in by_reel.items()
    })
