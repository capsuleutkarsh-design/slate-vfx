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


def leave_stage(roles=None, allowed_tabs=None) -> str:
    """
    The leave decision this person makes: "HR" (the final stage), "Supervisor"
    (the first, for their own reports - the approve_leave ability) or "".
    The Leave screen's queue and Home's leave panel both ask this.
    """
    if manages_leave(roles, allowed_tabs):
        return "HR"
    from .access import can
    return "Supervisor" if can(_as_list(roles), "approve_leave") else ""


def manages_it(roles=None, allowed_tabs=None) -> bool:
    """
    True for the people who work the IT queue (and the IT half of joining
    and leaving).

    Decided by the manage_it ability alone. It used to be true for the "IT"
    tab key as well, so one flag both opened the IT screens and took away the
    person's own "My tickets": an intern given Hardware lost the ability to
    raise a ticket, and a role with only manage_it got the queue but no IT
    screens. The tab key now only opens the screens (sees_it_screens). Roles
    that held the key were given manage_it once on upgrade, so nobody who
    worked the queue before stops working it.
    """
    perms = _permissions(allowed_tabs)
    if "all" in perms:
        return True
    from .access import can
    return can(_names(roles), "manage_it")


def sees_it_screens(roles=None, allowed_tabs=None) -> bool:
    """Hardware, Licences and Deployment: the "IT" tab key, or working the desk."""
    return has_permission(allowed_tabs, MANAGES_IT) or manages_it(roles, allowed_tabs)


def can_view_licences(roles=None, allowed_tabs=None) -> bool:
    """
    Licences, at least read-only: IT, or anybody with view_licences (a
    Production Head who approves renewals). Editing stays with the IT screens.
    """
    if sees_it_screens(roles, allowed_tabs):
        return True
    from .access import can
    return can(_names(roles), "view_licences")


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
