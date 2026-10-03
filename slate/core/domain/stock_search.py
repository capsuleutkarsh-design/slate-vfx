"""
What the stock library stores about an asset so it can be found again.

Search used to look at three things - the file name, the tag string and the
path - so the Help's promise that "4K" or "smoke" finds clips nobody named
that way was not true: resolution, frame rate, codec and category were never
searched at all (MED-026). And the tags themselves were written three
different ways (a list, "a,b", "['a', 'b']"), so one careless join turned
"Pending" into "P,e,n,d,i,n,g" for every row it touched (MED-003).

This module is the one place that decides both:

    normalise_tags(value)        -> ['Fire', 'Smoke']   (from any of the shapes)
    tags_text(tags)              -> 'Fire,Smoke'        (what the column holds)
    build_search_text(asset)     -> 'fire smoke 3840x2160 4k uhd 24fps h264 ...'
    escape_like(text)            -> the text with % _ \\ made literal for LIKE

Nothing here touches the disk or the database, so the ingest, the import, the
migration that repairs old rows and the tests all use the same rules.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Iterable, List

# Every visual tag the ingest can give a picture, in the order the gallery's
# Visual filter lists them.
VISUAL_TAGS = ("Dark", "Bright", "Warm", "Cold", "Green Screen", "Blue Screen")

# Tags that only ever meant "not analysed yet". They are never stored: an
# asset waiting for analysis has no tags, rather than a tag that lies.
PLACEHOLDER_TAGS = frozenset({"pending", "corrupt"})

_LIST_SHAPE = re.compile(r"^\s*\[(.*)\]\s*$", re.DOTALL)


def normalise_tags(value) -> List[str]:
    """
    A clean list of tags from whatever was stored or passed.

    Accepts a list or tuple, a comma-separated string, a JSON or Python-ish
    list written as text, or None. Empty items and duplicates (case-blind) are
    dropped, order is kept. A string that was once split into its letters and
    joined back ("P,e,n,d,i,n,g") is mended into the word it was.
    """
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        items = [str(v) for v in value if v is not None]
    else:
        text = str(value).strip()
        if not text:
            return []
        shaped = _LIST_SHAPE.match(text)
        if shaped:
            try:
                parsed = json.loads(text)
                if isinstance(parsed, list):
                    return normalise_tags(parsed)
            except (ValueError, TypeError):
                pass
            text = shaped.group(1)
        items = [part.strip().strip("'\"") for part in text.split(",")]
        letters = [p for p in items if p]
        # The damage MED-003 did: every character its own tag.
        if len(letters) >= 3 and all(len(p) == 1 for p in letters):
            items = ["".join(letters)]

    seen = set()
    clean: List[str] = []
    for item in items:
        tag = " ".join(str(item).split())
        if not tag:
            continue
        key = tag.lower()
        if key in seen:
            continue
        seen.add(key)
        clean.append(tag)
    return clean


def real_tags(value) -> List[str]:
    """normalise_tags() without the "Pending"/"Corrupt" placeholders."""
    return [t for t in normalise_tags(value) if t.lower() not in PLACEHOLDER_TAGS]


def tags_text(value) -> str:
    """What the tags column holds: 'Fire,Smoke' (placeholders left out)."""
    return ",".join(real_tags(value))


def visual_text(value) -> str:
    """The visual_tags column: only the known visual tags, in their order."""
    wanted = {t.lower() for t in normalise_tags(value)}
    return ",".join(t for t in VISUAL_TAGS if t.lower() in wanted)


def escape_like(text: str) -> str:
    """Make a typed search literal inside LIKE ... ESCAPE '\\'."""
    return (str(text or "")
            .replace("\\", "\\\\")
            .replace("%", "\\%")
            .replace("_", "\\_"))


_CAMEL = re.compile(r"([a-z])([A-Z])")
_SPLIT = re.compile(r"[\W_]+")


def name_words(text) -> List[str]:
    """
    The words of a name, lower case: 'FireBurst_v002-final.exr' ->
    ['fire', 'burst', 'v002', 'final', 'exr'].

    Split on anything that is not a letter or digit and on lower-to-upper
    case changes. One splitter for categories, tags and search, so a rule
    matches whole words and never the inside of one ('rain' in 'grain',
    'ember' in 'December', 'hd' in 'uhd' - MED2-005, MED2-026).
    """
    return [w for w in _SPLIT.split(_CAMEL.sub(r"\1 \2", str(text or "")).lower()) if w]


# Words in file names that say nothing about the picture (MED2-014).
STOP_WORDS = frozenset({
    "a", "an", "and", "the", "of", "for", "with", "to", "in", "on", "at", "by", "from",
    "file", "files", "name", "final", "copy", "min", "sec", "tex", "test", "tests",
    "new", "old", "version", "take", "temp", "tmp", "img", "dsc", "untitled",
})


def search_terms(query: str) -> List[tuple]:
    """
    What a search is made of, as (text, at_word_start) pairs; every one has
    to match. Words match the start of a word; something typed that holds no
    word at all ('_', '%') is looked for anywhere, literally.
    """
    terms = []
    for typed in str(query or "").lower().split():
        words = name_words(typed)
        if words:
            terms += [(w, True) for w in words]
        else:
            terms.append((typed, False))
    return terms


def _metadata(value) -> dict:
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(value) if value else {}
        return parsed if isinstance(parsed, dict) else {}
    except (ValueError, TypeError):
        return {}


def resolution_terms(width, height) -> List[str]:
    """
    How people describe a frame size: '3840x2160', '3840', '2160', '4k',
    'uhd', 'portrait'. Classes follow the long edge, so a 4096x1716 scope
    plate is 4K as well.
    """
    try:
        w, h = int(width or 0), int(height or 0)
    except (TypeError, ValueError):
        return []
    if w <= 0 or h <= 0:
        return []
    terms = [f"{w}x{h}", str(w), str(h)]
    long_edge = max(w, h)
    if long_edge >= 7680:
        terms += ["8k"]
    elif long_edge >= 5760:
        terms += ["6k"]
    elif long_edge >= 3840:
        terms += ["4k", "uhd"]
    elif long_edge >= 2048:
        terms += ["2k"]
    elif long_edge >= 1920:
        terms += ["hd", "1080p"]
    elif long_edge >= 1280:
        terms += ["720p"]
    else:
        terms += ["sd"]
    if h > w:
        terms.append("portrait")
    elif w == h:
        terms.append("square")
    else:
        terms.append("landscape")
    return terms


def resolution_class(width, height) -> str:
    """'4K', 'HD', ... for a badge; '' when unknown."""
    for term in resolution_terms(width, height):
        if term in ("8k", "6k", "4k", "2k"):
            return term.upper()
        if term == "1080p":
            return "HD"
        if term == "720p":
            return "720p"
    return ""


def build_search_text(asset: dict) -> str:
    """
    Everything an asset can be found by, lower case, one line.

    Name, tags, category, folder names, extension, resolution words, frame
    rate, codec and whether it is a still, a clip or a sequence.
    """
    asset = asset or {}
    words: List[str] = []

    def add(*items: Iterable):
        # Each item whole ('23.976fps') and in its words ('23', '976fps'):
        # a search matches the start of either (MED2-026).
        for item in items:
            text = str(item or "").strip()
            if text:
                words.append(text.lower())
                words.extend(name_words(text))

    path_text = asset.get("file_path") or asset.get("path") or ""
    path = Path(str(path_text)) if path_text else None
    add(asset.get("display_name"), asset.get("name"), asset.get("file_name"))
    if path is not None:
        add(path.stem)
        add(path.suffix.lstrip("."))
        try:
            add(path.parent.name, path.parent.parent.name)
        except (ValueError, IndexError):
            pass
    add(asset.get("category"))
    add(*real_tags(asset.get("tags")))
    add(*normalise_tags(asset.get("visual_tags")))

    meta = _metadata(asset.get("metadata"))
    add(*resolution_terms(meta.get("width"), meta.get("height")))
    try:
        fps = float(meta.get("fps") or 0)
    except (TypeError, ValueError):
        fps = 0.0
    if fps > 0 and not meta.get("is_still"):
        whole = f"{fps:.3f}".rstrip("0").rstrip(".")
        add(f"{whole}fps", whole)
    add(meta.get("codec") if str(meta.get("codec") or "").lower() != "unknown" else "")

    # The words the screen uses for the kind (the List view's Type column says
    # "Movie"), as well as the ones people say.
    if asset.get("is_sequence") or meta.get("is_sequence"):
        add("image sequence")
    elif meta.get("raw"):
        add("camera raw")
    elif meta.get("is_still"):
        add("still", "image", "picture")
    elif meta.get("duration_sec"):
        add("clip", "video", "movie")

    # One copy of each word, in first-seen order: shorter to store and search.
    seen = set()
    unique = []
    for word in " ".join(words).split():
        if word not in seen:
            seen.add(word)
            unique.append(word)
    return " ".join(unique)
