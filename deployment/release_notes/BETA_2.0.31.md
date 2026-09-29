# Slate BETA 2.0.31

Three installers, one per machine role. Install the server first, then the workstations.

| Installer | Install on | What it holds |
|---|---|---|
| `setup_Slate Server_vBETA 2.0.31.exe` | the one machine that runs the studio database | Slate Server, PostgreSQL 17, PgBouncer, the updater |
| `setup_Slate_Studio_vBETA 2.0.31.exe` | artist, lead and supervisor workstations | Slate Studio, ffmpeg, Olive, OpenRV, the updater |
| `setup_Slate_Ops_vBETA 2.0.31.exe` | HR, IT and production office machines | Slate Operations, ffmpeg, Olive, the updater |

Nothing needs an internet connection. Every dependency is inside the installer, and updates are picked up from the studio's shared folder.

## Upgrading from 2.0.29

Run the new installers over the old ones, server first. Settings and data are kept.

Then sign in as an admin on one workstation before everybody else starts. The first start adds a few things to the database, once: the new studio roles, a password-change flag on users, and the change feed that live updates use. It takes a few seconds, and doing it from one machine means fifty workstations do not all try at 9 am.

**Check your roles after upgrading.** A tab that needs a permission is now hidden unless one of the person's roles grants it. Before, somebody whose roles granted nothing saw every tab. Everyone still has Home, Attendance, Leave and IT Support. If somebody says a tab has gone, give their role that tab under Users & Roles → Roles & Permissions.

Update every workstation. An older workstation still works against the new database, but it does not get live updates.

**Licence.** From this release Slate is under the UT Community Licence 2.0. Copies up to BETA 2.0.29 keep the GPLv3.

## New in this release

**Roles**
- 16 studio roles are ready to use: IT, HR, Admin, Production Head, Production Coordinator, Roto Prep Supervisor, Comp Supervisor, Team Lead, Roto Artist, Paint Artist, Deage Artist, AI Artist, Compositor, DMP, CG and Editor. The old roles are still there and keep working. A role you delete stays deleted, and one you customised is not reset at the next start.
- Every tab can now be granted on the Roles & Permissions screen, grouped the way the sidebar is. Abilities that used to be fixed in code can be ticked per role: approve leave, manage leave, manage IT, manage users, edit permissions, see team attendance, ingest stock, force save, Excel sync, and more.
- Admin, IT, HR and Developer can edit permissions. Everybody else sees the screen read-only.
- Supervisors and Team Lead edit only their own department on the dashboard.
- A role that people still hold cannot be deleted. The screen says who holds it.

**Users & Roles**
- Users are managed in one place, the Users & Roles tab. The User Mgmt and Permissions pages have left Admin Panel, which now holds Live Ops, Audit Logs and Data Center.
- **Import users from Excel or CSV:** Users → Import from Excel / CSV. The file needs two columns, Username and Display Name; a template can be saved from the same screen. Choose one role and one first password for the whole list. People who already exist are skipped, and the screen shows who was added, who was skipped and why. Other details are filled in per person afterwards.
- Imported people must choose their own password the first time they sign in. Nobody else is made to change theirs.
- Anyone can change their own password: click your name in the header → Change password…
- Roles are picked from a drop-down with tick boxes, so one person can hold several.
- Export the user list to CSV.

**Live updates**
- Screens now show other people's changes within about five seconds, without restarting Slate:
  - IT Support: the queue and My Tickets, including replies
  - Leave: approvals and My Leave, including holiday changes
  - Joining & Leaving, Hardware and Licences
  - The VFX Dashboard
- A screen reads the database again only when its own data changed. A screen nobody is changing costs nothing.
- If the database has been unreachable, every screen reads once when it comes back, so nothing that changed meanwhile is missed.

**VFX Dashboard**
- The dashboard no longer reloads the whole project every time anybody saves. Only the shots that changed are read and updated in place.
- **Unsaved edits are never overwritten.** If somebody else saves a shot you are editing, your edits stay, the shot name gets a ⚠ and you are told to check it before saving.
- Your selection, scroll position and undo steps stay where they were.
- Nothing moves while you are typing in a cell or have a dialog open. The update happens straight afterwards.
- A database hiccup no longer empties the grid.

**Licence and credits**
- Studio, Operations and Server show a small credit line at the bottom: "Slate · © 2026 Utkarsh Tripathi · UT Community Licence 2.0". Clicking it opens the Credits screen.
- The licence files ship with every install, and the installers show the licence before installing.

## Fixed in this release

- A ticket raised on one workstation now appears in the IT queue on the others. It used to appear only after Slate was restarted.
- The dashboard could throw away edits that were typed but not yet saved, whenever anybody else saved anything in the project.
- Leave, joining & leaving, hardware and licence screens no longer show stale data until the tab is rebuilt.

## Known limits

- Updates take up to about five seconds to arrive. They are not instant.
- When a shot changes on the dashboard, its row stays where it is. Sort again to reorder.
- The shot detail panel on the right does not redraw itself. You are told when its shot has changed; open it again to see the latest.
- If somebody changes only a department's status on a shot you have unsaved edits on, saving does not show the conflict window. The ⚠ mark is your warning to check the shot first.
- If the database user is not allowed to create triggers, live updates are off. Screens then re-read every 30 seconds, and the dashboard checks every 3 seconds, as before.
