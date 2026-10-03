"""
apply_hardening_step(): the only way a hardening step should be applied.

    from slate_server.core.recovery.hardening import apply_hardening_step

    result = apply_hardening_step(
        "strict_pg_hba",
        apply=lambda: write_new_rules(),                 # the change itself
        precheck={"db": db, "hba_text": proposed},       # can_still_get_in(**precheck)
        verify=lambda: can_still_get_in(client=..., server=...),   # after, for real
        rollback=None,                                   # extra undo beyond the files
        switch="strict_pg_hba", mode="log_only")
    if not result.applied:
        show(result.message)

In order:
    1. can_still_get_in(**precheck)  - "no" means nothing is touched at all;
    2. before_security_change(name)  - the snapshot to go back to;
    3. apply();
    4. verify()                      - with real connections, after the change;
    5. if apply() raised or verify() said "no": rollback(), then the snapshot's
       files are put back and the database re-reads them. The step reports
       what went wrong and that it was undone;
    6. only then is the switch recorded (on or log_only).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class StepResult:
    name: str
    applied: bool
    message: str
    snapshot: Optional[Path] = None
    rolled_back: bool = False
    details: List[str] = field(default_factory=list)


def apply_hardening_step(name: str, apply: Callable[[], object], *, precheck: dict,
                         verify: Optional[Callable[[], object]] = None,
                         rollback: Optional[Callable[[], object]] = None,
                         switch: Optional[str] = None, mode: str = "on",
                         layout=None, switch_db=None, by: str = "") -> StepResult:
    from slate.core.security import switches
    from slate.core.security.precheck import can_still_get_in
    from . import snapshots

    before = can_still_get_in(**precheck)
    if not before.ok:
        return StepResult(name, False, before.message)

    if layout is None:
        from .layout import find_layout
        layout = find_layout()
    snap = snapshots.before_security_change(name, layout)

    failure = ""
    try:
        apply()
    except Exception as exc:
        failure = "The change failed: %s" % (str(exc).splitlines()[0] if str(exc) else exc)
    if not failure and verify is not None:
        after = verify()
        if not getattr(after, "ok", bool(after)):
            failure = getattr(after, "message", "The check after the change failed.")

    if failure:
        details = []
        if rollback is not None:
            try:
                rollback()
                details.append("Undid the change.")
            except Exception as exc:
                details.append("The undo step failed: %s" % exc)
        try:
            details += snapshots.restore_snapshot(layout, snap)
        except Exception as exc:
            details.append("Putting the files back failed: %s - use Recover Slate, "
                           "'Restore last snapshot'." % exc)
        logger.error("Hardening step %s was undone: %s", name, failure)
        return StepResult(name, False, failure + " Everything was put back as it was.",
                          snap, True, details)

    if switch:
        switches.set_mode(switch, mode, by=by or "hardening", db=switch_db,
                          note="applied by %s" % name)
    logger.warning("Hardening step %s applied (snapshot %s).", name, snap.name)
    return StepResult(name, True, "Applied. Snapshot %s can undo it." % snap.name, snap)
