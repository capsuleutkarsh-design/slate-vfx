"""
Who sees which side of the workplace modules.

Leave and Ticketing each have two entirely different jobs behind one sidebar
entry. An artist wants to ask for something and then find out what happened to
it. HR and IT want a queue of other people's requests to work through. Showing
either side a toolbar built for the other is what made these screens read as
broken - every one of them was the manager's screen, so an artist had nothing
to do on it and nobody could create the records the manager's screen lists.

There is no toggle. The view you get is decided by what you are.
"""

from __future__ import annotations


# The permission that grants the managing side of each module.
MANAGES_LEAVE = "HRMS"
MANAGES_IT = "IT"


def _as_list(roles) -> list:
    if not roles:
        return []
    if isinstance(roles, str):
        return [roles]
    return list(roles)


def _names(roles) -> set:
    return {str(r).strip().lower() for r in _as_list(roles) if r}


def _permissions(allowed_tabs) -> set:
    return {str(p).strip().lower() for p in (allowed_tabs or [])}


def has_permission(allowed_tabs, permission: str) -> bool:
    perms = _permissions(allowed_tabs)
    return "all" in perms or str(permission).strip().lower() in perms


def manages_leave(roles=None, allowed_tabs=None) -> bool:
    """
    True for the people who action other people's leave.

    Role names are honoured as well as the permission, so a studio that adds an
    "HR" account without editing permissions still gets the right screen.
    """
    if has_permission(allowed_tabs, MANAGES_LEAVE):
        return True
    # The role names come from access.json, not from here. This module used
    # to carry its own literal set, which is how Attendance, Users & Roles and
    # Leave each ended up with a different idea of who HR is.
    from .access import can
    return can(_names(roles), "manage_leave")


def manages_it(roles=None, allowed_tabs=None) -> bool:
    """True for the people who work the IT queue."""
    if has_permission(allowed_tabs, MANAGES_IT):
        return True
    from .access import can
    return can(_names(roles), "manage_it")


# ---------------------------------------------------------------------------
#
# The vocabularies used to live here as well, which meant leave types and
# ticket priorities were defined in two places at once - exactly the drift this
# codebase has just spent a week removing from its stylesheets. They now live
# with the rules that use them:
#
#     leave types, statuses, accrual, the sandwich rule   slate/core/domain/leave_policy.py
#     ticket categories, the priority matrix, SLA         slate/core/domain/service_desk.py
#
# Import from those. Nothing about policy belongs in an access-control module.
