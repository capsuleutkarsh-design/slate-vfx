"""
Reading what a licence server reports, from a saved text file.

Peak use only means something if it is written down at busy moments, and
nobody types numbers at the busiest moment of the day. Both licence managers
VFX studios run can print their current use as text, and a scheduled task (or
IT, by hand) can save that text to a file:

    FlexLM / FlexNet   lmutil lmstat -a -c <port@server>  > lmstat.txt
    RLM                rlmutil rlmstat -a -c <port@server> > rlmstat.txt

This module turns either into readings - product, seats in use, seats issued.
It never talks to a licence server itself and runs nothing; it only parses
text somebody saved.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, List, Optional


@dataclass
class ServerReading:
    product: str
    in_use: int
    total: Optional[int]


_LMSTAT = re.compile(
    r"Users of\s+(?P<product>[^:\s]+)\s*:\s*\(\s*Total of\s+(?P<total>\d+)\s+licen[sc]es?\s+issued\s*;"
    r"\s*Total of\s+(?P<used>\d+)\s+licen[sc]es?\s+in use", re.IGNORECASE)

# RLM: a product line ("nuke_i v2023.0910") then, on it or the next lines,
# "count: 10, # reservations: 0, inuse: 3, exp: ...".
_RLM_PRODUCT = re.compile(r"^\s*(?P<product>[A-Za-z0-9_.\-]+)\s+v\d[\w.\-]*", re.IGNORECASE)
_RLM_COUNTS = re.compile(r"count\s*:\s*(?P<total>\d+).*?inuse\s*:\s*(?P<used>\d+)", re.IGNORECASE)


def parse_lmstat(text: str) -> List[ServerReading]:
    """'Users of nuke_i:  (Total of 10 licenses issued;  Total of 3 licenses in use)'."""
    out = []
    for match in _LMSTAT.finditer(text or ""):
        out.append(ServerReading(match.group("product"), int(match.group("used")),
                                 int(match.group("total"))))
    return out


def parse_rlmstat(text: str) -> List[ServerReading]:
    out = []
    product = None
    for line in (text or "").splitlines():
        head = _RLM_PRODUCT.match(line)
        counts = _RLM_COUNTS.search(line)
        if head and not line.strip().lower().startswith(("count", "license server", "isv")):
            product = head.group("product")
        if counts and product:
            out.append(ServerReading(product, int(counts.group("used")), int(counts.group("total"))))
            product = None
    return out


def parse(text: str) -> List[ServerReading]:
    """Whichever format the text is in; readings of the same product are added up."""
    readings = parse_lmstat(text) or parse_rlmstat(text)
    merged = {}
    order = []
    for r in readings:
        key = r.product.lower()
        if key not in merged:
            merged[key] = ServerReading(r.product, 0, 0)
            order.append(key)
        merged[key].in_use += r.in_use
        merged[key].total = (merged[key].total or 0) + (r.total or 0)
    return [merged[k] for k in order]


def _squash(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(text or "").lower())


def match_licence(product: str, licences: Iterable[dict], total: Optional[int] = None) -> Optional[dict]:
    """
    The licence a server product most likely is: the same name, or the one
    licence whose name starts the product's ('Nuke' for 'nuke_i'). None when
    it is not clear - two contracts of the same product, or nothing alike.
    """
    licences = list(licences)
    wanted = _squash(product)
    exact = [l for l in licences if _squash(l.get("software_name")) == wanted]
    candidates = exact or [l for l in licences if _squash(l.get("software_name"))
                           and wanted.startswith(_squash(l.get("software_name")))]
    if len(candidates) == 1:
        return candidates[0]
    # Several contracts of one product: the server's issued count tells them
    # apart when exactly one contract has that many seats.
    if candidates and total is not None:
        sized = [l for l in candidates if int(l.get("total_seats") or 0) == int(total)]
        if len(sized) == 1:
            return sized[0]
    return None
