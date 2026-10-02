"""
Everything a role can be granted: the tabs it opens and the things it may do.

One list, read by both the sidebar (main_window_builder) and the Permissions
screen (role_editor). The screen used to carry its own hand-written list of
eleven tabs, which fell behind as tabs were added - Scheduling, Bidding, the
IT tabs and Users & Roles could not be granted from it at all.

A role's permissions are one JSON list in ut_roles.permissions:
    "Dashboard", "IT", ...        tab keys (see TABS)
    "can:dashboard_write", ...    abilities (see ABILITIES)
    "ALL"                         every tab and every ability
Keys this module does not know are kept as they are when a role is saved, so
nothing another version wrote is ever lost.
"""

from __future__ import annotations

from dataclasses import dataclass

ALL = "ALL"
ABILITY_PREFIX = "can:"


@dataclass(frozen=True)
class Tab:
    key: str            # what ut_roles stores - never rename, it would revoke it from every role
    label: str          # what the screen shows
    group: str          # VFX, Operations or System
    opens: str          # the sidebar entries this key opens


TABS = (
    Tab("Folder Creator", "Build & Ingest", "VFX", "Build & Ingest"),
    Tab("Rename Tool", "CAP Rename", "VFX", "CAP Rename"),
    Tab("Stock Browser", "Stock Viewer", "VFX", "Stock Viewer"),
    Tab("Shot Review", "Timeline Viewer", "VFX", "Timeline Viewer"),
    Tab("Dashboard", "VFX Dashboard", "VFX", "VFX Dashboard"),
    Tab("Scheduling", "Scheduling", "VFX", "Scheduling"),
    Tab("Bidding", "Bidding", "VFX", "Bidding"),
    Tab("IT", "IT & Infra", "Operations",
        "Hardware, Licences, Deployment, the IT desk and the IT side of Joining & Leaving"),
    # Label only - the key "HRMS" is what every role has stored.
    Tab("HRMS", "HR (people, leave, joining)", "Operations",
        "Users & Roles, the HR leave queue and the HR side of Joining & Leaving"),
    Tab("Admin Panel", "Admin Panel", "System",
        "Live Ops, Audit Logs and Data Center"),
    Tab("Tester Panel", "Tester Panel", "System", "Tester Panel (developer / QA tool)"),
    Tab("Settings", "Settings", "System", "Settings"),
)

TAB_GROUPS = ("VFX", "Operations", "System")

# Open to everybody, whatever their role: nothing to grant.
ALWAYS_OPEN = ("Home", "Attendance", "Leave", "IT Support")


@dataclass(frozen=True)
class Ability:
    key: str            # the action name, as in access.json
    label: str
    help: str


ABILITIES = (
    Ability("dashboard_write", "Edit the dashboard",
            "Edit shots, statuses and assignments on the VFX Dashboard."),
    Ability("department_scoped", "…only their own department",
            "Limits dashboard editing to the columns of the department on the person's job title."),
    Ability("artist_own_status", "Set own shot status",
            "Set the status (YTS, WIP, READY, SENT FOR REVIEW) of work they are assigned to."),
    Ability("approve_leave", "Approve leave (first stage)",
            "Work the supervisor stage of the leave queue."),
    Ability("manage_leave", "HR leave stage",
            "Work the HR stage of the leave queue, keep the holiday calendar and close the leave year."),
    Ability("manage_it", "Work the IT desk",
            "Work the IT service desk and the IT half of joining and leaving."),
    Ability("manage_users", "Manage users",
            "Create, edit and deactivate user accounts, giving roles no more powerful "
            "than their own."),
    Ability("manage_permissions", "Edit roles and permissions",
            "Use this Permissions screen to change what roles can do - only handing on "
            "what they hold themselves."),
    Ability("view_team_attendance", "See team attendance",
            "See the whole studio's attendance, correct punches and export timesheets."),
    Ability("ingest_stock", "Ingest stock",
            "Ingest into and delete from the stock library."),
    Ability("force_save", "Force-save over others",
            "Overwrite another user's edit when a conflict is reported."),
    Ability("excel_sync", "Excel import / export",
            "Import from and export to the project Excel backup."),
    Ability("wipe_fleet_caches", "Wipe fleet caches",
            "Clear thumbnail and proxy caches on every connected workstation."),
    # Added 2026-09. Existing roles were upgraded once so nobody lost what
    # they could already do (UserManager._upgrade_role_abilities).
    Ability("assignable", "Can be given shots",
            "Listed in the dashboard's Artist pickers, so work can be assigned to them."),
    Ability("dashboard_view_all", "See every shot",
            "See all shots of a project on the dashboard, not only their own."),
    Ability("schedule_write", "Edit the schedule",
            "Add, change and shift milestones on Scheduling."),
    Ability("approve_bid", "Approve bids",
            "Mark a bid Won or Lost."),
    Ability("delete_project", "Archive or delete projects",
            "Archive or delete a whole dashboard project."),
    Ability("view_licences", "See licences",
            "Open Licences read-only."),
    Ability("manage_system", "System administration",
            "Data Center, table editing, the SQL console, Audit Logs and remote workstation actions."),
    Ability("studio_settings", "Studio settings",
            "Change studio-wide settings: studio policy, server and database, branding, updates."),
    Ability("tester_destructive", "Destructive test tools",
            "Use the Tester Panel's wipe, file-date, large-generator and VACUUM tools."),
)

# Only Admin and Developer may grant these (or Full access). Anybody else
# with "Edit roles and permissions" can hand on only what they hold, and never
# these - they are how somebody would give themselves everything else.
SENSITIVE_ABILITIES = frozenset({
    "manage_permissions", "manage_users", "wipe_fleet_caches", "manage_system",
    "studio_settings", "tester_destructive", "delete_project",
})

# What makes one person's account more powerful than another's, for giving
# roles to people: rights over the system and over other people's access and
# records. A non-admin may give somebody a role only if they hold every one of
# these the role carries - so HR can take on a compositor or a supervisor, but
# not make anybody a Developer, an Admin or an IT desk worker.
#
# Admin Panel and Tester Panel are not on the list: what is dangerous inside
# them is behind manage_system and tester_destructive, which are - a
# supervisor's Admin Panel is read-only Live Ops.
PRIVILEGED_TABS = frozenset({"IT", "HRMS"})
ADMIN_ABILITIES = SENSITIVE_ABILITIES | frozenset({
    "manage_leave", "manage_it", "view_team_attendance",
})

# Restrictions rather than rights: "ALL" does not switch these on.
RESTRICTIONS = frozenset({"department_scoped"})

# What "ALL" does not switch on: the restrictions, and "assignable" - being
# given shots is part of somebody's job, not a right that Full access implies.
# Through ALL every Developer and the admin account were offered as artists.
NOT_IMPLIED_BY_ALL = RESTRICTIONS | frozenset({"assignable"})

TAB_KEYS = frozenset(t.key for t in TABS)
ABILITY_KEYS = frozenset(a.key for a in ABILITIES)


def ability_key(action: str) -> str:
    return ABILITY_PREFIX + action


def abilities_in(permissions) -> set:
    """The ability names in a stored permission list ("can:x" -> "x")."""
    found = set()
    for p in permissions or []:
        p = str(p).strip()
        if p.lower().startswith(ABILITY_PREFIX):
            found.add(p[len(ABILITY_PREFIX):].strip().lower())
    return found


def has_all(permissions) -> bool:
    return any(str(p).strip().upper() == ALL for p in (permissions or []))


# ---------------------------------------------------------------- new roles
# The studio's roles, created once on first start (see UserManager). After
# that they belong to the studio: edits and deletions are kept.

_VFX_ARTIST_TABS = ["Dashboard", "Stock Browser", "Rename Tool", "Settings"]
_OWN_STATUS = [ability_key("artist_own_status"), ability_key("assignable")]
_SUPERVISOR_ABILITIES = [ability_key(a) for a in (
    "dashboard_write", "department_scoped", "artist_own_status", "approve_leave",
    "ingest_stock", "force_save", "excel_sync", "dashboard_view_all")]

STUDIO_ROLES = {
    "Admin": [t.key for t in TABS if t.key != "Tester Panel"]
             + [ability_key(a.key) for a in ABILITIES
                if a.key not in RESTRICTIONS | {"assignable", "tester_destructive"}],
    "IT": ["IT", "Settings",
           ability_key("manage_it"), ability_key("manage_permissions"),
           ability_key("studio_settings"), ability_key("view_licences")],
    "HR": ["HRMS", "Settings",
           ability_key("manage_leave"), ability_key("manage_users"),
           ability_key("view_team_attendance"), ability_key("manage_permissions")],
    "Production Head": ["Dashboard", "Shot Review", "Scheduling", "Bidding", "Stock Browser",
                        "Folder Creator", "Rename Tool", "Settings"]
                       + [ability_key(a) for a in (
                           "dashboard_write", "artist_own_status", "approve_leave",
                           "view_team_attendance", "ingest_stock", "force_save", "excel_sync",
                           "dashboard_view_all", "schedule_write", "approve_bid",
                           "view_licences")],
    "Production Coordinator": ["Dashboard", "Shot Review", "Scheduling", "Stock Browser",
                               "Folder Creator", "Rename Tool", "Settings"]
                              + [ability_key(a) for a in (
                                  "dashboard_write", "artist_own_status", "excel_sync",
                                  "dashboard_view_all", "schedule_write")],
    # access.json gives producer dashboard_write and dashboard_view_all; without
    # a role row a Producer had no Dashboard tab at all.
    "Producer": ["Dashboard", "Shot Review", "Scheduling", "Stock Browser",
                 "Folder Creator", "Rename Tool", "Settings"]
                + [ability_key(a) for a in (
                    "dashboard_write", "artist_own_status", "excel_sync",
                    "dashboard_view_all", "schedule_write")],
    "Roto Prep Supervisor": ["Dashboard", "Shot Review", "Stock Browser", "Rename Tool", "Settings"]
                            + _SUPERVISOR_ABILITIES,
    "Comp Supervisor": ["Dashboard", "Shot Review", "Stock Browser", "Rename Tool", "Settings"]
                       + _SUPERVISOR_ABILITIES,
    "Team Lead": ["Dashboard", "Shot Review", "Stock Browser", "Rename Tool", "Settings"]
                 + [ability_key(a) for a in (
                     "dashboard_write", "department_scoped", "artist_own_status",
                     "approve_leave", "ingest_stock", "assignable")],
    "Editor": ["Shot Review", "Stock Browser", "Dashboard", "Rename Tool", "Settings",
               ability_key("artist_own_status"), ability_key("dashboard_view_all")],
    "Roto Artist": _VFX_ARTIST_TABS + _OWN_STATUS,
    "Paint Artist": _VFX_ARTIST_TABS + _OWN_STATUS,
    "Deage Artist": _VFX_ARTIST_TABS + _OWN_STATUS,
    "AI Artist": _VFX_ARTIST_TABS + _OWN_STATUS,
    "Compositor": _VFX_ARTIST_TABS + _OWN_STATUS,
    "DMP": _VFX_ARTIST_TABS + _OWN_STATUS,
    "CG": _VFX_ARTIST_TABS + _OWN_STATUS,
}
