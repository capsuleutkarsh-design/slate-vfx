"""
Settings data repairs (system area).

one_working_week: the studio's week was stored twice - the studio policy's
'weekly_offs' (Attendance, Leave) and the money card's working_hours 'days'
(IT's response clocks) - and the two could disagree. The policy's weekly offs
are the one value now. A studio that only ever set its working days on the
money card has them carried over as weekly offs; a policy that already names
its weekly offs is left as it is (Attendance has been counting from it). The
old working_hours value is kept untouched.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def one_working_week(db):
    from slate.core.infra.studio_settings import StudioSettings
    store = StudioSettings(db)
    policy = store.get("attendance_policy") or {}
    if "weekly_offs" in policy:
        return "the studio policy already names its weekly offs; kept"
    if not store.is_set("working_hours"):
        return "no working days were saved; the policy's default week stands"
    try:
        days = {int(d) for d in (store.get("working_hours") or {}).get("days") or []}
    except (TypeError, ValueError):
        return "the saved working days could not be read; left alone"
    offs = sorted(set(range(7)) - days)
    if not days or not offs:
        return "the saved working days leave no weekly off; left alone"
    policy = dict(policy, weekly_offs=offs)
    result = store.set("attendance_policy", policy, by="Slate (one working week)")
    if not result:
        logger.warning("The working week was not moved to the studio policy: %s", result.error)
        return False
    return f"weekly offs {offs} taken from the working days"
