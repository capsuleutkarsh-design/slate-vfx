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

# What a part of a stitch is called. Anything else after the shared name - a
# second shot code (EP01_SH010 / EP01_SH020), a version (SH010_v2) or a state
# (SH020_final / SH020_temp) - is not a part, however short it is.
_PART_WORDS = {"left", "right", "l", "r", "lt", "rt", "fg", "bg", "mg", "top", "bottom",
               "upper", "lower", "front", "back", "near", "far"}
_PART_PATTERN = re.compile(r"^(?:[a-z]|pt\d{1,2}|part\d{1,2}|p\d{1,2}|\d{1,2})$", re.IGNORECASE)
# A prefix made only of episode / sequence codes does not name a shot.
_GROUPING_CODE = re.compile(r"^(?:ep|episode|sq|seq|sc|scene|e|s)\d+$", re.IGNORECASE)


def is_part_marker(token: str) -> bool:
    token = str(token or "")
    return token.lower() in _PART_WORDS or bool(_PART_PATTERN.match(token))


def _is_numbered_only(token: str) -> bool:
    """'1', '02': a part marker, but a weak one - it is also how takes are numbered."""
    return str(token or "").isdigit()


def _names_a_shot(prefix: List[str]) -> bool:
    if not _HAS_DIGIT.search("".join(prefix)):
        return False
    return not all(_GROUPING_CODE.match(t) for t in prefix)


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
    # False when the parts are only told apart by a bare number (SH010_1 /
    # SH010_2): offered, but not ticked, because takes are numbered that way.
    confident: bool = True

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
    # as "plate_one" and "plate_two" would fuse - and an episode code on its
    # own (EP01) is not a shot either.
    if not _names_a_shot(prefix):
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
        if tail and (len(tail[0]) > MAX_TAIL_LENGTH or not is_part_marker(tail[0])):
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

    # Only names that could pair are compared: a part is "<shot>_<marker>",
    # so parts are bucketed by the shot they would belong to, and a whole
    # plate joins the bucket of its own name. Comparing every name with every
    # other took 1.5 s for 1,500 folders, on the UI thread.
    buckets: Dict[Tuple[str, ...], List[str]] = {}
    wholes: Dict[Tuple[str, ...], List[str]] = {}
    for name in names:
        tokens = [t.lower() for t in _tokens(name)]
        wholes.setdefault(tuple(tokens), []).append(name)
        if len(tokens) >= 2 and is_part_marker(tokens[-1]):
            buckets.setdefault(tuple(tokens[:-1]), []).append(name)

    shot_names: Dict[str, str] = {}
    weak: Dict[str, bool] = {}
    for key, parts in buckets.items():
        members = parts + [w for w in wholes.get(key, []) if w not in parts]
        for i, left in enumerate(members):
            for right in members[i + 1:]:
                shot = _pair_is_stitch(left, right)
                if not shot:
                    continue
                union(left, right)
                root = find(left)
                current = shot_names.get(root)
                if current is None or len(shot) < len(current):
                    shot_names[root] = shot
                tails = [_tokens(n)[len(_tokens(shot)):] for n in (left, right)]
                if any(t and _is_numbered_only(t[0]) for t in tails):
                    weak[root] = True

    clusters: Dict[str, List[str]] = {}
    for name in names:
        clusters.setdefault(find(name), []).append(name)

    groups = []
    for root, parts in clusters.items():
        if len(parts) < 2:
            continue
        shot = shot_names.get(root) or min(parts, key=len)
        groups.append(StitchGroup(shot_name=shot, parts=sorted(parts), reel=reel,
                                  confident=not weak.get(root, False)))

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
    The stitches a client drive appears to contain, worked out by the same
    survey the ingest runs on (slate.core.domain.ingest_survey), so what a
    coordinator is asked to confirm is what would actually be moved.
    """
    from slate.core.domain.ingest_survey import survey_drive

    return survey_drive(source_path, target_reel_name).stitch_groups
