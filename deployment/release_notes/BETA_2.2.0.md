# Slate BETA 2.2.0

**BETA 2.2.0 replaces 2.0.32.** It holds everything prepared for 2.1.0 (never released on its own), plus a security release and fixes for bugs that real tools found.

Three installers, one per machine role. Install the server first, then the workstations.

| Installer | Install on | What it holds |
|---|---|---|
| `setup_Slate Server_vBETA 2.2.0.exe` | the one machine that runs the studio database | Slate Server, PostgreSQL 17, PgBouncer, Recover Slate, the updater |
| `setup_Slate_Studio_vBETA 2.2.0.exe` | artist, lead and supervisor workstations | Slate Studio, ffmpeg, OpenRV, the updater |
| `setup_Slate_Ops_vBETA 2.2.0.exe` | HR, IT and production office machines | Slate Operations, ffmpeg, the updater |

Nothing needs an internet connection. Every dependency is inside the installer, and updates are picked up from the studio's shared folder.

## Before you install

1. Back up the studio database (Slate Server → Operations → Back up now).
2. Install **Slate Server 2.2.0** on the server PC. The first start shows the **Recovery Key once**. Write it down and keep it with a printed copy of `docs/RECOVERY.md`. It is the way back in if nobody can sign in.
3. Install Studio or Ops 2.2.0 on one HR or admin workstation, sign in, and check Users & Roles and Attendance. Then install the rest.

The database is upgraded on the first start, as described under **Upgrading from 2.0.32** below.

## What this release adds, in short

- **Everything prepared for 2.1.0.** Every screen was gone through twice, and about 1,750 problems were fixed. This includes a Light theme, one way to save on the VFX Dashboard, a Gantt timeline, bid line items, ₹ INR money and the Timeline Viewer's own player. See **Also in this release** below.
- **Security:** the holes found in the 5 October review are closed, without any way to lock you out. Fixes that could stop somebody signing in sit behind switches that start **off**.
- **Errors are no longer hidden** where they cost money or data: leave and comp-off balances, the attendance export, the stock library, permissions and backups.
- **Bugs found by testing with the real tools:** RV, ffmpeg, PostgreSQL, the share and the updater. Several features had never worked.
- **Slate Server backs up on its own** every day, and keeps to the retention set on the Operations screen.
- **Olive is removed.** The Timeline Viewer plays the lineup in RV and exports EDLs for editorial.
- **OpenRV is updated to 4.0.2.** The 2.0.0 build shipped before was missing RV's Python plug-ins, so no RV add-on, including the Slate verdict menu, could load.

## Security

2.2.0 closes the security holes found in the review of 5 October 2026. It is built so that it **cannot lock you out**:

- **A fix that cannot stop anybody signing in is simply made.**
- **A fix that could stop somebody is behind a switch, and every switch starts OFF.** You turn switches on yourself, one at a time, from **Recover Slate** on the server PC with the Recovery Key. Turning one on first checks that people can still get in. It takes a snapshot so it can be undone, and it is rolled back by itself if the check afterwards fails.
- **The Recovery Key is the way back in when nothing else works.** Read `docs/RECOVERY.md` and keep a printed copy with the key.

### Fixed in this release (no switch needed)

**The server**
- **Slate Server no longer starts the web API.** Anyone on the network could use it to make themselves an administrator. Nothing in Slate used it. The "Open web dashboard" button and the "Web API port" setting are gone. The web API itself is removed (see **Removed**).
- **"Disconnect session" disconnects the session you selected.** The list showed one set of connections while the button acted on another, so it could end the wrong person's session. The list also refreshes without freezing the window.
- **Settings say "NOT saved" when they were not saved.** This includes the database password. A damaged `slate_server_config.json` is no longer overwritten; you are told to fix it or move it aside.

**Accounts and sign-in**
- **A `users.json` or `roles.json` on the share is read only once, on an empty database.** Before, anyone who could write to the share could make themselves a Developer, or reset admin's password, at the next start of any PC.
- **The shared `admin123` master password no longer approves a fleet-wide restart or shut down.** Admins confirm with their own password, and the setting is removed from the default configuration.
- **The test accounts artist/artist123 and tester/tester123 are no longer created** on a new database.
- **Old password formats are upgraded at sign-in.** A password stored as plain text, or in the old unsalted format, is re-stored securely the next time that person signs in.
- **Every sign-in is recorded** in the audit log: success, and each failure with the reason, but never the password.
- **The audit trail is in the database, and nothing can change or delete it.** Workstations can only add lines and read them; the database refuses edits and deletions, even by accident, and stamps each line with the server's clock. Only when the database cannot take a line (an older server, an outage) does it go to the file on the share, as before, and the Audit Logs screen shows both. A share that is down no longer slows signing in. Slate Server sets this up when it starts, so **start Slate Server 2.2.0 once before the workstations**.
- **Resetting someone's password needs 8 characters and makes them choose their own at their next sign-in.** New passwords need 8 characters. Existing shorter passwords still work.
- **A wrong password waits one second before you can try again.** Nobody is ever locked out for wrong tries.
- The sign-in screen no longer names the default login.

**Data Center, exports and the network**
- **Password hashes, tokens and secrets are hidden** in the Data Center grid, search and SQL results. The users table is read-only there; change people in Users & Roles, which protects the last administrator.
- **The SQL console is read-only.** One statement at a time; nothing it runs can change data.
- **Text that looks like a formula** (`=`, `+`, `-`, `@`) is written as text in the dashboard's Excel backup and in fleet reports, so opening them cannot run anything. Restoring the backup brings the text back exactly.
- **Workstations no longer listen for commands on port 5006.** That listener accepted anything from anyone and did nothing useful.
- **A workstation keeps the server it was set up with.** Another PC answering the server search can no longer quietly take its place. If the server really moves to a new address, use **Reconfigure server / database** on the sign-in screen.
- **Folders built from stored shot and project names must stay inside the project folder.** A name that would lead outside it is refused.
- New workstations are set up with the studio's app account (`ut_vfx_app`), never the database superuser.

### Switches you can turn on (all OFF after installing)

Open **Recover Slate** on the server PC, unlock it with the Recovery Key, and use **Security switches**. Each switch has **Log only**, which logs what it would refuse and refuses nothing, and **Turn on**. Try **Log only** for a day first and read the log. Any switch can be turned off again from the same page, even when the database is down.

| Switch | What it does when on | Before turning it on |
|---|---|---|
| `split_superuser_password` | The database superuser gets its own password, kept on the server PC only. Workstations keep theirs. | Press **Restart pool** afterwards. |
| `strict_pg_hba` | The superuser can connect only from the server PC. Workstations can reach only the studio database. | Refused while a workstation is connected as the superuser. |
| `refuse_inactive_signin` | People who are deactivated, or past their last working day, cannot sign in. `admin` and `developer` are always exempt. | Check Users & Roles for anyone wrongly marked as left. |
| `no_plaintext_passwords` | Passwords in the old formats no longer open an account. | Refused while any active account still has one: let everybody sign in once first. |
| `no_default_accounts` | admin/admin123 is never created or brought back. The first-run screen points to Recover Slate instead. | Make sure you have the Recovery Key. |
| `no_sqlite_fallback` | When the server is down, workstations do not open the local copy, which has its own admin/admin123. | With this on, nobody can work while the server is down. |
| `hide_password_hashes` | Password hashes leave the accounts table for a part of the database the workstations cannot read; the database checks passwords itself. The Data Center, the SQL console and tricks like `row_to_json` show nothing. | **Every workstation must run 2.2.0 first:** an older Slate cannot check a hidden password. A workstation cut off from the server cannot sign anybody in from its local copy while this is on. Turning it on proves a real sign-in before and after with a temporary account, and that no stored password changed; turning it off moves the hashes back. |
| `signed_fleet_commands` | Broadcasts, restarts and shut downs carry a signature; workstations ignore any command file without a valid one. Anyone who can only write to the share can no longer send them. | Every admin's Slate must be 2.2.0. The admin types their own password once per session before the first command; only active administrators get the studio's signing key. Use **Log only** first and look for "would be refused" in the workstations' logs. |
| `signed_updates` | Workstations install only updates signed with the owner's release key. | Make the release key and ship its public half in a build first (`docs/development.md`, "Signing updates"); turning this on is refused until the build has one. The installer always works by hand. |

`pgbouncer_hba` is listed but not built yet; turning it on is refused.

### Still open (planned)

- **The database password `Tango$`** is still the shipped default. It is public. Changing it safely is planned for **2.3**:
  1. workstations read the password from Windows' protected store;
  2. the new password is rolled out to every workstation;
  3. it is changed on the server;
  4. it is removed from the repository's history.

  Doing it in any other order disconnects every PC.
- **Permissions are checked in the app, not by the database.** Anyone with the database password can bypass them, including making themselves an administrator, which would also get them the fleet signing key. This changes after the password is protected.
- **The workstations' database account still owns the studio's tables,** so someone with the database password could drop or empty them (backups are the way back). Taking that away means the database changes Slate makes on upgrade must run on the server instead of the workstations; planned with the password change. It already cannot create accounts, databases or roles.

## Found by testing with the real tools

These were checked against the real programs, not stand-ins, and each check now stays in the test suite.

**RV**
- **Review in RV and the Timeline Viewer's Open in RV never opened RV.** Slate used an RV command that does not exist. They now open RV, with several clips as one playlist.
- **The Slate menu inside RV failed on every verdict.** It also sent back the wrong frame number (1 instead of 1001) and never exported notes. It is fixed and now ships with the installer.

**Pictures and playback**
- **Dashboard shot thumbnails were always the red placeholder.** They looked one folder too shallow under `01_Scan`.
- **A sequence with a missing frame** made a short proxy and stopped the player early. The gap now holds the previous frame.
- **ACES EXRs showed as Rec.709**, and sRGB was never offered in the colour menu.
- **A proxy older than its render** kept playing. "Rebuild all" now really rebuilds.

**Server, backups and the share**
- **Restoring a backup left every workstation with "permission denied".** The restored tables now keep their owner.
- **Restart pool on Slate Server always failed.** It works now.
- **Broadcast messages from the Admin Panel never arrived.** Two commands sent in the same second also overwrote each other.
- **The settings file written by `setup.bat` was ignored, password included,** because of an invisible marker at its start. Every reader now accepts it.
- **The updater could delete a file it had just installed** when only its capitalisation had changed.
- **If the server's Cache folder can't be written,** Slate now says so instead of saving per-PC picture paths into the shared library.

**Screens**
- **The dashboard's Advanced filter crashed on opening.** Batch edit never applied. The filter chip's Clear left the grid filtered. A verdict given in the shot panel's Versions box never moved the shot.
- **Several buttons did nothing:** Retry on the offline banner, "Test connection" on first run and Reconfigure, and the bidding tracking Export.
- **The Slate menu in Nuke, Natron, Blender and Silhouette** now finds its shot.
- **Settings "Project root"** now reaches Build & Ingest.
- **Timeline Viewer** opens your project, not the first one alphabetically.
- **Exports:** a few PDF, CSV and Excel exports broke on special characters. Excel restore lost shot notes.
- **Saving an archived project's settings** no longer brings it back.

## Errors that are no longer hidden

- **Leave:**
  - If the holiday list cannot be read, a leave request is not saved, instead of charging the holidays as leave days.
  - Overlapping leave cannot slip through the same way.
- **Comp-off:**
  - A day cannot be credited twice. Duplicates already in the ledger are relabelled, not deleted, so balances do not change; HR can review them.
  - Approving or cancelling leave changes the comp-off balance completely, or not at all.
- **Year end** names anyone it could not close.
- **Attendance:** the monthly export is refused with a message if holidays or approved leave could not be read, instead of marking those days Absent.
- **Joining and leaving:** a leaver's last day, or a joiner's start date, that cannot be saved is reported.
- **Stock library:**
  - An ingest stops with a message if the library cannot be read, instead of bringing deleted assets back.
  - Assets whose analysis could not be saved are counted as failed.
- **Permissions:** a brief database hiccup no longer makes everybody "not allowed".
- **Dashboard:** rows that cannot be read are named instead of vanishing.
- **Workstation backups** are recorded in the audit trail.
- **Build & Ingest** asks before starting when free disk space is unknown.

## Slate Server

- **Daily backups run on their own.** Every six hours, and five minutes after start, Slate Server backs up when the last backup is over a day old and the database is up. Old backups are then removed by the retention on the Operations screen: older than 30 days by default, always keeping the newest 7. Closing the server waits for a running backup.
- **Recover Slate** (Start menu, or the Recovery page) needs the server PC and the Recovery Key, never a Slate password. From there you can:
  - reset or create an administrator;
  - repair the database passwords;
  - turn security switches on or off;
  - put back the last snapshot.

  Slate never removes, demotes or switches off the last administrator.

## One rule each

These were decided in several places that disagreed. Each is now decided once, so the screens agree:
- **whether someone is still active**, so that leave, Users & Roles, attendance, the pickers and sign-in agree;
- **who reports to whom;**
- **punching in.** Home now warns about approved leave as Attendance does, and punching never freezes the window. Closing Slate or signing out still never punches you out, and punching in again the same day is still allowed;
- **the department a supervisor sees;**
- **the database password a workstation uses.** It is found exactly as before.

The test database no longer has its own copy of the dashboard's save code, so the tests now check the code the studio runs.

## Removed

- **Olive.** It is no longer developed. The Timeline Viewer now:
  - plays the lineup inside Slate;
  - opens it in RV ("Open in RV");
  - exports EDLs for Resolve, Premiere or Avid ("Export EDL").

  The Studio installer is about 70 MB smaller.
- **Wipe caches** in the Admin Panel. It never reached any workstation. The Wipe fleet caches permission went with it.
- **The web API**, with the Admin Panel's "Start API gateway" and Slate Server's web dashboard. Nothing in Slate used it, and it was a way into the studio network. Two web libraries (FastAPI and uvicorn) went with it.
- **Code that nothing used,** including an unused "similar assets" search.

## Known limits of 2.2.0

- **The automatic punch-in at sign-in** still punches in on a day of approved full-day leave.
- See also **Still open** under Security, and the **Known limits** from 2.1.0 below.

---

# Also in this release (prepared as 2.1.0)

BETA 2.1.0 was prepared but not released. Everything below is part of 2.2.0. Where it describes the upgrade, it is the upgrade from 2.0.32 you are making now.

### Highlights

- **A real Light theme.** Settings → Theme → Dark or Light. Dark is still the default and looks as it always has.
- **One way to save on the VFX Dashboard.** Every edit waits as a pending change you can undo. One **Save N changes** button saves them, and Slate asks before you close with unsaved edits.
- **Scheduling has a Gantt timeline** and a People view. Milestones can now be edited, deleted and given owners.
- **Bidding has line items**, revisions, Won/Lost decisions, cost tracking against the dashboard, and PDF/Excel export. Money is in **₹ INR** by default.
- **Build & Ingest copies by default.** Move is an option. A check runs before every ingest so you see what will happen first.
- **CAP Rename works again**, with a real **Undo last rename**.
- **Stock Viewer**: favourites, Studio picks, sound in previews, search by "4K" or "24fps", and Undo after delete.
- **Timeline Viewer** has its own preview player, opens the lineup in **RV**, and exports **EDLs** for editorial. Olive is removed: it is no longer developed.
- **IT Support** clocks count working hours (10:00–19:00 on the studio's working days), so a ticket raised on Saturday evening is not "breached" on Monday morning.
- **Closing Slate or signing out never punches you out.** You can also punch in again after punching out, as a second session that day.
- **Smaller laptops work.** The window fits 1366×768 and 1280×720 screens, and pages scroll instead of being cut off.
- **The sign-in window opens at once, even when the database is down.** It connects behind the window and says so if it cannot. Before, Slate could show nothing for minutes and then crash.
- **One working week for the whole studio.** The weekly offs in Studio policy now drive leave, attendance and the IT Support clocks alike.

### Upgrading from 2.0.32

**Back up the studio database before you upgrade.** This release changes the database (see below). The changes keep your data and remove nothing, and they were tested on both PostgreSQL and the local database, but one of them changes a column type on a live table. A backup is the safe way back if anything goes wrong.

1. Back up the database: **Slate Server → Operations → Back up now** (see "Backing up and restoring the database" in the README).
2. Run the new **Server** installer over the old one.
3. Start **one** workstation (Studio or Ops) first and sign in. The database changes are made by the first Slate that starts against the upgraded database, and that first start can take a little longer than usual. Use an HR or admin machine if you can (see *Studio-wide settings* below).
4. Then install on the other workstations. Settings and data are kept.

#### What changes in the database

All changes add things. No table is removed.

- **Scheduling dates become real dates.** On PostgreSQL, milestone start and end change from text to `DATE`. Every row is rewritten in one date format first. Any date that cannot be read is kept in a new `legacy_dates` column instead of being lost. Links to milestones that no longer exist are cleared.
- **New tables:**
  - bid line items;
  - studio-wide settings;
  - a record of the one-time database repairs;
  - stock favourites, Studio picks and stock ingest folders;
  - licence renewal reminders;
  - comp-off spends, so comp-off days come back when leave is cancelled;
  - hardware history: each machine's status changes and renames.
- **New columns:**
  - **bids:** currency, client, day rate, discount, tax, revisions, notes, archive, and who sent or decided them and when. Bid amounts are stored as exact money (`NUMERIC`) on PostgreSQL.
  - **milestones:** owner, department, effort and completion date.
  - **shot tasks:** "actual days" per department.
  - **shot change history:** which shot, reel and department each change was made to.
  - **leave requests:** separate supervisor and HR notes, which half of a half day, and withdrawal details.
  - **joining/leaving lines:** who ticked them and when, and a round number, so a re-hire gets a fresh checklist.
  - **user accounts:** active / deactivated, and a service-account flag (the `admin` and `tester` accounts get it).
  - **IT:** licence cost, vendor, contract and notes; machine type, serial number, asset tag, purchase and warranty dates; and, on tickets, when the promise is measured from once IT raise the priority.
  - **stock library:** category, visual tags, sequence frame range, who added it, and soft delete.
- **Attendance day details** become a proper JSON column on PostgreSQL. Rows that the old code had glued together are merged back first.
- **Notification times** keep their seconds on PostgreSQL (the column is widened, values kept).
- **The old IT licence table** is copied once into the current licence list (name, seats and expiry). The old table is kept and marked legacy.
- **Attendance refreshes itself** when punches change, like the other live screens. The database hook for this is added on the first start.

#### One-time repairs, made on the first start

These run once and are never repeated:
- attendance days the old code had saved badly;
- shot status and priority columns that had drifted out of step;
- old shot history, so it names the shot it belongs to;
- bid budgets that the old storage had rounded;
- old leave decision notes, split into supervisor and HR notes;
- employment values, which become Staff / Freelance / Contract;
- ticket statuses and priorities, which are put into one spelling;
- machine statuses, and junk specs such as "None GB" or "N/A" (now blank);
- stock tags that had been split into single letters (`P,e,n,d,i,n,g`), and "Pending" tags (rebuilt from the file path);
- placeholder thumbnail paths saved into shots;
- notifications that were stored under a display name;
- dashboard "actual days" that every save used to write as 0. They become blank ("not recorded"), so Bidding's tracking no longer treats them as real figures;
- the shot **Artist**, which used to be filled in from the Comp department every time a project was read. It is written down once as shown, so clearing it now sticks;
- the stock library: categories are worked out again from each file's path, filler words ("with", "file", "tests"…) and the category are taken out of the automatic tags (tags people typed are kept), and the search index is rebuilt. No media files are opened;
- machines with an old typed-in owner and no loan record. If the name is a known person, the machine gets an open loan to them, dated the upgrade day and marked "upgrade". Otherwise it becomes Available, and the old text is kept as a tooltip;
- the studio's working week (see *Studio-wide settings* below).

On each machine, a settings file damaged by the old `&` / apostrophe bug is also repaired once.

#### Studio-wide settings

- **Studio policy now lives in the database, so every workstation uses the same rules.** It covers the late cut-off, the standard day, the auto punch-out time, weekly offs, accrual, carry-forward, the sandwich rule, comp-off and project rest. Before, each machine had its own copy.
  - The **first** machine to start after the upgrade gives its old values to the whole studio. Check them afterwards in **Settings → Studio policy**.
  - HR and admins can change the policy. Everyone else sees it read-only.
- **One working week.** The **weekly offs** in Studio policy are now the studio's working week for leave, attendance *and* the IT Support clocks. The "Working days" boxes on the Studio currency, rates and hours card are gone; that card keeps only the start and end of the working day.
  - Once, on the first start: if working days were saved on that card and the policy names no weekly offs, they are copied into the policy as weekly offs. A policy that already names weekly offs wins, because Attendance has been counting from it.
- **Currency is ₹ INR by default**, with Indian digit grouping (₹2,07,00,000.00, or ₹2.07 Cr in short form).
  - **Existing bids keep their numbers and are marked USD**, because the old screens showed "$".
  - New rupee bids get 18% GST by default, which you can change per bid. USD, EUR and GBP bids start with no tax.
  - Day rates start at ₹8,000 / $300 a day. In **Settings → Studio currency, rates and hours**, people who approve bids can change the currency, day rates and GST, and admins, developers and IT can change the working hours and the licence renewal warning.
- **Studio name** is a new setting in **Bidding settings**. It is the title of bid PDFs.
- **Build & Ingest templates are studio-wide.** Custom templates are kept in the database, so every coordinator sees the same ones. The database copy wins, and each machine keeps a copy for working offline. The first machine to start after the upgrade shares the templates it has.

#### Roles and permissions

- **Roles only gain rights on upgrade.** Once, on the first start:
  - roles that open Scheduling may now edit it;
  - roles that open Bidding may now decide bids;
  - roles that open Bidding get the new **Edit bids** right (see below);
  - artist-type roles can be given shots;
  - supervisors, coordinators, producers and heads see every shot on the dashboard.
- **New "Edit bids" right.** Making and changing bids now needs it. Every role that has the Bidding tab is given it on the first start, so nobody loses the right to make bids. A role without it sees Bidding read-only. New roles get it by default for Admin, Developer, Production Head, Production Coordinator and Producer.
- **Bidding settings:** margins, complexities, day rates, GST and the studio name are changed by the people who approve bids, the same rule as Settings → Studio currency, rates and hours.
- **The Producer role gains the VFX Dashboard tab.** It also keeps Excel export now that it is no longer treated as a supervisor.
- **Some defaults changed:**
  - **Supervisors** no longer manage users, and no longer see the whole studio's attendance. They see the attendance of everyone under them (their reports, and their leads' reports), read-only.
  - In the **Admin Panel**, supervisors get **Live Ops, read-only**. Audit Logs, Data Center and the remote machine actions are for Admin and Developer.
  - If a studio wants supervisors to keep the old rights, an admin can tick "Manage users" or "See team attendance" on the role.
- **Coordinators are no longer treated as supervisors** on the dashboard. They cannot force a save over someone else's edit unless "Force-save over others" is ticked on their role.
- Someone who edits roles can now only hand on what they have themselves. Only Admin and Developer can give Full access.
- Full access no longer puts Developers and the admin account in the dashboard's Artist pickers.
- **Hardware and Deployment need "Manage IT" to change anything.** Someone who has the screen but not that right sees it read-only, as Licences already was.
- **Artists** no longer see Reports → Deliveries (a package lists every shot of the show), and the Review queue shows them their own shots only.
- **Help** shows pages only for the screens you can open.
- **Licence renewal reminders** go to IT and to the people who can view Licences.

#### Behaviour you may rely on that has changed

- **The sign-in window opens at once, even when the database is down.** It connects behind the window. If it cannot, it says so with **Try again**, and nothing else is started until you are connected. The "Database Fallback Active" box before sign-in is gone; the sign-in window says when Slate is working on this computer's copy. Esc no longer quits Slate from the sign-in window.
- **Closing Slate or signing out never punches out.** The end-of-day auto punch-out closes forgotten days. After a punch-out, **Punch in** starts a second session the same day. The `admin` and `tester` service accounts are no longer punched in when they sign in.
- **Build & Ingest copies plates by default** and leaves the client drive as it was. **Move** is still there as an option. "Overwrite Existing" is gone.
- **Build & Ingest reads a drive differently:**
  - a folder named after a format or size (`EXR`, `MOV`, `DPX`, `PROXY`, `4K`, `1920x1080`…) **belongs to the shot above it**. `SH_010/EXR` and `SH_010/MOV` are one shot, SH_010, not shots called "EXR" and "MOV";
  - LUT, reference, audio, edit-list and paperwork folders and files are **client material**, filed in the client folder, never shots. So are loose files at the top of the drive, though the check before ingest can turn them into a shot;
  - the whole drive is read, however deep the folders go;
  - copies arrive as `<name>.partial` and get their real name only once checked, so a copy that dies half-way never leaves a short plate in the project;
  - **Move** removes the client-drive folders it emptied (only empty ones);
  - a dry run writes nothing to the database;
  - a stopped run can be finished later into the same scan version (**Finish the stopped run**);
  - your answers to the stitch question are remembered per project.
- **CAP Rename no longer writes `undo_rename_*.bat` files** next to your plates. Use **Undo last rename** in the tab. The undo record is kept in Slate's own folder.
- **CAP Rename is all or nothing.** If one file cannot be renamed (for example, it is open somewhere), none are. Each person sees and undoes only their own renames (admins and developers see everyone's), and the newest 50 are kept.
- **Delete on the dashboard is now Archive.** An archived project is hidden, can be restored, and keeps its history. Admin and Developer also get **Delete permanently**, which asks you to type the project code. Deleting permanently also removes the project's versions, review notes and delivery packages, so a new project with the same code starts clean.
- **Dashboard saves need an active project.** If someone archives or deletes a project while you have edits open, your save is refused with a message (and **Restore** for the people allowed), instead of quietly bringing the project back.
- **Review verdicts change the shot.** Approving or retaking a version in the Review queue, the review player or the shot panel puts the matching status on the shot as a pending edit, saved with **Save N changes**, which records history and tells the artist.
- **Add project** asks only for the code, name and folder. A new project's backup is Slate's own Excel passbook; **Edit project** can still point it at another `.xlsx`/`.xlsm` file. "Create blank template" is gone, because Slate never read the sheet back.
- A **new delivery** offers the latest approved version of each shot and department by default.
- **Hardware:** a machine that was ever issued is **retired** instead of deleted. Retired, Lost and Disposed machines are hidden unless you tick **Show retired**.
- **Users & Roles:** **Deactivate** replaces Delete for anyone with history. A deactivated person is hidden from lists and pickers and left out of year end. Delete is only for accounts with no history. Leave stops building up after a person's last day.
- **Stock Viewer: deleted assets stay deleted.** Delete hides the asset and offers Undo. After 24 hours its cached thumbnail and proxy, favourites and picks are removed, but Slate remembers the file was deleted, so **Rescan** and ingest no longer bring it back. People who can ingest into the library find deleted assets under **Removed** and can restore them at any time. Importing an exported library still brings a deleted file back, because that is a deliberate act. **Clear library** asks you to type `CLEAR` and forgets everything, including the deleted list and the ingest folders.
- **Stock search matches the start of words only.** "plosion" no longer finds "explosion", and "HD" no longer finds "UHD". Names, tags, category, folders, resolution, frame rate, codec and kind ("movie", "image sequence") are all searched. A term with no letters or digits (`_`, `%`) is still matched anywhere.
- **Stock stills play the original file.** EXR, HDR and DPX stills are no longer shown from an 8-bit proxy, so the colour controls appear. Proxies are used only for movies and image sequences. Sound and 3D files are not taken into the library; the ingest summary names them.
- **Timeline Viewer** reads each movie plate's frame rate and length from the file. Image sequences are played and written at 24 fps, because Slate has no project frame-rate setting yet.
- **New shot names follow one rule everywhere:** Add Shots, Build & Ingest, CAP Rename's stitch names and "Create shots from bid". Names may use letters, digits, `_`, `-` and `.`, with no spaces, up to 64 characters. Shots you already have are never refused.
- **Olive is removed.** It is no longer developed, so the Timeline Viewer no longer writes Olive timelines or opens Olive. **Open in RV** plays the lineup instead, and **Export EDL** writes CMX 3600 EDLs to `<project>/editorial/lineups` for Resolve, Premiere or Avid. Old `.ovexml` files there are left alone. The Studio installer is about 70 MB smaller.
- **Auto-publish** fires only when a shot changes to Approved, and asks first. On new projects the output folder is `08_Deliver`. It copies only the shot's current (else newest) version folder, and only media files, keeping sub-folders.
- **Bidding:**
  - **A revised Won bid stays Won** until the new revision is decided. Its money still counts in the Won card and its tracking stays open; the table says "Won – v2 in progress". When v2 is decided, v1 becomes Superseded. (Revising a Lost bid works the same way. Revising a Sent bid still supersedes it at once.) Only the newest revision can be revised or decided.
  - **Price** now always means the price *before* discount, and **Subtotal** the amount after discount. The bid list, the CSV and Compare used to call the after-discount figure "Price".
  - A bid that was ever sent or decided can no longer be deleted, even after **Reopen as draft**.
  - Bid PDFs say "valid until" 30 days after they are made.
- **Comp-off needs half a standard day.** Working a weekly off or a holiday earns comp-off only from half the standard day (4.5 h by default). Open, missing or auto-closed days never earn it. Final approval of Comp Off is refused when the person does not have enough comp-off left for that date.
- **Leave dates are checked** against the joining date, the last working day and leave years HR has closed. A half day never charges the days off around it under the sandwich rule.
- **Approving leave that leaves someone overdrawn** now says so and suggests rejecting and asking for Unpaid instead.
- **Deactivating a manager** offers to move their team to a new manager. **Reactivate** offers to clear a last day that has passed. A **re-hire** gets a fresh joining checklist.
- **IT Support:**
  - **"Thanks, works now!" no longer reopens a ticket.** A requester's reply on a Resolved or Closed ticket is passed to IT, and the reply box says so. **Reopen** is the button, and it asks what is still wrong.
  - **Waiting on requester** needs a note saying what IT need. The requester sees it.
  - **Raising a ticket's priority restarts its promise from now**, and P1/P2 tell IT. Lowering it keeps the original start.
- **Licence import** only ticks products whose names match exactly. Near matches are offered unticked, so render and other feature counts are no longer added to the main licence.
- **Settings → "Back up a project" is removed.** It copied the whole project onto this PC's system drive, zipped it and locked it with this PC's key, with no size check, no Cancel and no way to restore. Project folders live on the studio file server and belong to its backup. Slate's own data is backed up by the Slate Server.
- **Tester Panel: the config sandbox is removed.** Parts of Slate set up at start-up (Live Ops, the fleet commands, the audit log) kept the real server folder while the banner said otherwise, and it could not be made safe. To test against another setup, start Slate with another config.
- **Tester Panel files** go into a `Slate_tester` folder inside the folder you pick; a folder that already holds anything is never taken over. Library folders (Desktop, Documents, Downloads…) and network paths are refused. The Workflow simulation shows its total size first; runs over 1 GB are for Developers, every run keeps 10% of the disk free, and runs over 5 GB ask first.
- **Live Ops** judges whether a machine is still reporting by the time its status file was written on the shared folder, so a workstation with a wrong clock no longer looks online. Problem machines are listed first, and admins can **Remove from Live Ops** a machine that is gone for good (it comes back if it reports again).
- **The sidebar's operations group is called PEOPLE** in both apps ("People" in Help).
- **The Google Sheets sync is removed**, with the `gspread` and `oauth2client` packages it needed. Nothing in Slate used it any more.
- **Slate Server window:** **Restore from file…** asks you to type `RESTORE`, and **Apply update** says how many workstations are connected and offers **Restart now** or **Later**.
- **Projects live in the database only.** The bundled sample project and the **AK74 client sheet are removed**.
- **Smart search is removed.** Typing "?" in the dashboard search used to run a hidden AI search. Now it is the ordinary search, which reaches every column. Its model download and the `fastembed` package it needed are gone.
- **Notifications have one bell in the header** for everybody. The dashboard's "Alerts" button and the Timeline Viewer's bell are gone.
- **The header Sync button only appears when Slate is working offline.** It pushes your offline changes to the studio database.
- **Ctrl+1 to Ctrl+9 open the 1st to 9th screen** in your sidebar. Ctrl+K opens the command palette, and Ctrl+P / Ctrl+R still work.
- **Signing out and back in reopens the same app** (VFX or Operations), not the all-in-one window.
- **Settings has one Save** for the whole page. A theme change finishes when you restart Slate. "Max Concurrent Threads" is removed because nothing used it.
- **EXR and OpenImageIO previews are now settings, on by default.** Before, they depended on how Slate was launched.
- **IT staff** get both the IT queue and their own tickets. **Production Head** can read Licences.
- **Deployment** is a log of what was installed where. Slate does not push software, and the screen and Help now say so.
- **Closing the Slate Server window** asks first, and offers to keep the server running in the tray.

### New in this release

#### Sign-in & window

- **Sign-in** opens in about a second and connects in the background.
  - Messages wrap and are never cut off. They sit under the Sign in button, so the button no longer jumps.
  - When the database is down, a **Try again** button reconnects without restarting, and the window stays responsive while it tries.
  - New: a show-password toggle and a Caps Lock hint.
  - The cursor starts in the first empty field.
- **First-run setup** asks before saving a shared folder this computer cannot reach.
- **Header:**
  - your name, job title and avatar are one account menu, with Change password, Keyboard shortcuts, About Slate and Sign out;
  - "Search or jump to… Ctrl+K" opens the command palette;
  - a long name is shortened instead of disappearing;
  - the badge says VFX or OPS;
  - **LOCAL MODE** shows whenever Slate is working offline.
- **Sidebar:**
  - groups fold only when you fold them, and stay that way next time;
  - in the icon-only rail, folded groups still show;
  - every entry has an icon and a tooltip;
  - Home sits at the top, outside the groups, and the operations group is called **PEOPLE** in both apps;
  - a folded group's count is a dim outline, so it no longer looks like an unread badge.
- **Command palette** lists your real screens, including those in folded groups, in sidebar order, and matches the start of words.
  - **Jump to a shot** in any active project, even on a fresh start: Slate opens the project first. "shot 042" finds SH042, and "SH010" no longer opens SH0100.
  - It says when nothing matches, and a click outside closes it.
- **Notifications open what they are about:** double-click one to open the shot, or the IT Support or Licences screen.
- **About Slate** in the account menu shows the version, and the version is written the same way everywhere.
- A message can carry both an action (such as **Undo**) and **Details**.
- **Footer:** status messages sit on the left. Running tasks show there too ("Copying plates 37%"), and you can click them to see the task list.
- **Closing, signing out or syncing** asks first if a screen has unsaved edits or a job still running, such as an ingest or a rename.
- **Messages** appear as small notes above the footer. Many have a button, such as **Undo** or **Open folder**. Error boxes say what went wrong in plain words, with **Try again** and **Copy details for IT**.
- When the database cannot be reached, screens say **"Can't reach the studio database. Your work is safe…"** with Try again. When a screen could not load, it says so, instead of showing an empty list.
- **Workspace Info** shows the version, install folder, database and server, shared folder (and whether it can be reached, checked in the background), settings folder, all your roles and plugins, with **Copy details**.
- **Diagnostics** (Ctrl+Shift+D) shows exactly the same facts, with a Copy button.
- **Updates:** when an update is found, a note appears with **See what's new**. "Remind me later" really waits a day, and the "check on start-up" setting is honoured.
- Starting Slate again while it is already open brings the open window to the front.
- Slate opens about twice as fast after sign-in, because screens are loaded when you first open them.
- The window remembers its size and position.
- **Help:**
  - search reads page text;
  - groups match the sidebar;
  - clearing a search returns you to the page you were on, and Esc clears the search before it closes Help;
  - it lists only the screens you can open;
  - every page has been rewritten to describe what the screen really does today.
- **Slate Server window:**
  - asks before closing while the database runs, with an option to keep running in the tray. Stopping the server on close shows "Stopping the database…" instead of freezing;
  - the Projects and Stock Assets cards work;
  - Analytics fits smaller windows;
  - updates download in the background. **Apply update** says how many workstations are connected, with **Restart now** or **Later**;
  - **Restore from file…** sits apart from **Back up now** and asks you to type `RESTORE`;
  - **Open Web Dashboard** waits until the server is running, and the data folder is shown even while the server is off.

#### Home

- **My recent shots** are your own shots (as lead or in any department), newest first, with their project. Click one to open its project on the dashboard with the shot selected. **See all my shots** opens the dashboard's My shots view.
- **Leave on Home follows the Leave screen's rules:** artists see only their own leave, supervisors see their reports' (without the leave type), and HR see everyone's. Rejected and cancelled requests are left out, and "Leave to decide" counts only what is waiting on you.
- **AT A GLANCE** figures you can act on, depending on who you are:
  - artists see their open and in-review shots;
  - overseers see shots waiting for review;
  - operations staff see who is online, leave to decide and leave in the next two weeks;
  - IT see open tickets;
  - whoever can see Licences gets **Licences to renew**, shown in amber when something is due, with the names in the tooltip;
  - figures are in plain text, with amber only for things waiting on you. "Shots waiting for review" counts active projects only.
- **Tiles** appear only for screens you can open, and they all work, including Leave and IT Support.
- **Punch panel** shows "In since 09:04 (2 h 10 min)", **Punch in again** after a punch-out, and a **Fix a punch** link to Attendance. **Punch out** asks first ("Punch out at 18:42?"), and punching runs in the background.
- Home loads in the background. If the database is down, it says so with Try again instead of "Loading…" forever. It refreshes every minute and with F5.
- It works on 768-pixel-high screens and without graphics acceleration.

#### VFX Dashboard

- **One save model:**
  - every edit (grid, board, batch edit, detail panel) is pending and can be undone with Ctrl+Z;
  - **Save N changes** saves only the shots you changed;
  - the unsaved count is always visible;
  - Slate asks before closing or signing out with unsaved edits.
- **Redo** (Ctrl+Y or Ctrl+Shift+Z). An Undo button on a note undoes only that note's change, never a different one. The save button counts changes: "Save 6 changes (1 shot)".
- **Artists' own status changes save at once**, with an Undo note.
- **Conflicts:** when someone else saved the same shot, the conflict window shows who and when, your edits and theirs. You can **Keep theirs and re-apply mine**, or overwrite only the clashing shots. Every shot is saved under a version check in one go, so a save made in between is never overwritten silently.
- **Review:**
  - the Review queue holds only versions that were actually sent for review, with a Reel column;
  - review notes are signed by the person who wrote them;
  - the **Feedback** tabs show the version notes, with **Add note**;
  - versions are kept per reel, so SH010 in R01 and SH010 in R02 have separate versions;
  - leads can approve or retake only their own department's versions.
- **Deliveries:** a new delivery offers the latest approved versions, with **Show every version**, search, and a warning before anything unapproved goes in. A package is saved whole or not at all.
- **Excel backup** has every column (targets, In/OS, edit status, description, previous version and each department's actual days), priority names, real dates, a frozen header and a filter. It is written in the background, so Save no longer waits while someone has the file open in Excel.
- **Reel and Shot Name stay frozen on the left** while you scroll across departments.
- **Filters:**
  - search covers every column;
  - column filters take several values and show a funnel;
  - a "N filters on – Clear" chip shows when filters are active;
  - switching project starts clean;
  - "Showing X of Y" tells you how much is shown.
- **Board** has one column per real status (plus "No status"), with correct counts. Leads edit their department in the grid, not by dragging cards.
- **Target dates** sort as dates, and overdue or due-soon shots are marked.
- **Detail panel:**
  - an **Actual** days column per department, which Bidding's tracking reads;
  - Apply sends only what you changed;
  - the same rights as the grid;
  - closing it, pressing Esc, switching shot or closing Slate asks about changes you have not applied.
- **Toolbar** folds into **More** on narrow screens and has a List | Board toggle. Shortcuts: Ctrl+S, Ctrl+F, Esc, Space (Quick Look), F2.
- **Add shots** checks names, needs a reel, refuses duplicates and tells you which names it skipped. **Filters** (formerly "Advanced Query Builder") only offers fields that really exist.
- **Add project** checks the code (upper case, no spaces or slashes, no case-duplicates) and refuses the code of an archived project.
- **Production summary** says what it covers ("Filtered: 33 of 262") and leaves omitted shots out.
- **Shot history** shows real names and fields for that one shot. It records every field you change (scope of work, target, priority, version, frames, type, bid and actual days, not only status and artist), uses the grid's column names, and can load more.
- The project you opened last is remembered.

#### Build & Ingest and CAP Rename

**Build & Ingest**
- **A check before every ingest.** The client drive is surveyed in the background, and stitches are offered first. Then a summary lists every shot, with names you can edit, Copy or Move, and warnings. Nothing starts until you confirm it.
- **Copy by default**, with checksum verification and long-path support. Move is an option.
- **Format sub-folders** (`EXR`, `MOV`, `4K`…) are read as part of the shot above them, and client material (LUTs, reference, audio, edit lists, paperwork) is filed in the client folder instead of becoming shots. The check before ingest marks clashing rows in red, can be sorted, and can add shots that are already in the project to the Dashboard.
- **Pause and Stop** act between files, and a stopped run is reported as stopped. **Finish the stopped run** later brings the missing files into the same scan version.
- **Retry failed files** runs in the background and stays available after the run. It now also adds the shots it brought in to the Dashboard, fills the version's Denoise folders, and rewrites the delivery report. Failures of earlier runs are offered too.
- **Templates… menu** lets you create, edit, duplicate and delete templates. Templates are shared by the whole studio, and a template can say where client deliveries go ("Client deliveries go in").
- **The result stays on screen** after a run, with **Open report** and **Open folder**. The log is kept across runs, follows the current run, can show problems only, and can be saved.
- **The delivery report** counts only frames that really arrived, and lists "Sequences not fully brought in".
- Running the same drive again copies only what is new, and says "Nothing new on this drive" when there is nothing.
- Stitch answers are remembered for the project, so only new groups are asked about.
- **Ingest locks:** the lock is kept fresh while a run is open, even when paused. You can clear your own leftover lock; anyone can clear a stale one after a question; admins can clear any.
- Shot folders named in Hindi or another non-Latin script get a readable spelled-out name, marked "check it", instead of a bare number.
- The screen fits 1280×720.

**CAP Rename**
- **Undo last rename**, which brings back swapped and shifted names exactly.
- **Add files / Add folder**, drag and drop, **Remove selected**, **Clear list**, natural sort, sort by date, and Move up / Move down.
- **Keep extension** is on by default. Sanitize keeps letters in any script (Hindi, accents).
- Every file in a name collision is marked as a conflict. A bad pattern shows its reason under Find in plain words. A new name that would make the path too long is a conflict before you rename.
- **All or nothing:** if one file cannot be renamed, the others go back and nothing is renamed.
- **Undo** runs in the background with progress and Cancel. Each person has their own undo list.
- Sanitize keeps the dot before a frame number (`plate.1001.exr` stays as it is).
- The preview shows 11 rows on a 1366×768 laptop instead of 2.
- Renames and undos are written to the studio audit log, including the files that failed.

#### Stock Viewer and Timeline Viewer

**Stock Viewer**
- **★ Favourites** for each person, plus a shared **Studio picks** list that leads and supervisors can mark.
- **Previews play with sound**, with mute and volume.
- **Search reaches resolution, frame rate, codec, category and folder names**, so "4K", "1920" or "24fps" all work.
- **Tags** can be added, edited and removed.
- **Sort and filters** work across the whole library, and the total is always right.
- **Grid or List** view, where List is a sortable table.
- The inspector shows codec, size, category and who added the asset when, with Copy path and Show in Explorer.
- **Snapshot** saves at full source resolution, and always the frame on screen.
- **Quick Look** fits the screen, plays with sound, Space plays and Esc closes.
- **Player:** speeds from 0.25× to 4×, `,` and `.` step a frame, M mutes, and a file that cannot play shows a plain message with **Details**. Image sequences show their real frame numbers (1001–1024), and playback stops on the last frame.
- **Ingest** takes several folders at once and ends with a summary. **Rescan folders** checks the folders again. Visual tags (Green Screen, Blue Screen, Warm, Cold, Dark, Bright) are rebuilt and much faster: about 70 s for 450 items, where it used to take 500 s.
  - The summary names files it left out (sound, 3D) and files it could not read. An unreadable file shows "Could not read" instead of "Analysing…" forever.
- **Categories** match whole words: "film_grain" is no longer Liquids, and an `.exr` is no longer HDRI just for being an EXR.
- Delete can be undone, and the **Removed** view brings deleted assets back at any time. Clear library asks you to type `CLEAR`.
- **Export** writes a library file any studio can import: no database ids, favourites or this machine's cache paths.
- The inspector follows the list: after Reload, a search or a category change it keeps the asset if it is still listed, and clears it if not.
- The grid always fills the row, and the count reads "350 assets" unless something narrows the list.

**Timeline Viewer**
- **A built-in preview player:** a strip of the lineup in edit order. You can choose which layer of each shot to show, and **Play lineup** plays shot after shot. Review proxies are used when they exist, and **Make proxy** creates one for a heavy EXR shot when you ask.
- **Lineup table** with include boxes, reel filter and search. Shots without a scan are greyed out.
- Shots are ordered by reel, then by every number in the name.
- **Plate frame rate and length** are read from movie plates, so a 25 fps plate says 25 and the mismatch warning works. Shots of unknown length are drawn hatched in the strip.
- Ticking shots keeps the shot you were watching, and changing layer during **Play lineup** keeps playing.
- Every department gets a layer once it has a render, including DMP, CG, Roto, Matchmove and Slapcomp.
- **Open in RV** plays the ticked shots in RV, in edit order, at the layer chosen beside the player (the plate where a shot has no render of it yet). **Use proxies in RV** plays up-to-date review proxies instead of the frames.
- **Export EDL** writes the ticked shots as EDLs of that layer, one per reel and one with every reel. Each event names its clip and file for relinking; a sequence keeps its frame numbers as source timecode.
- Where a department keeps versions (`Output
002`, or `_v002` in the file name), the newest is shown. Before, `v001` came first by name.

**RV picker** shows layer, version and frames in a table, with tick boxes you can see in Dark and **Tick all / Untick all**. A render's version is read from its file name when nothing else gives it.

#### Scheduling and Bidding

**Scheduling**
- **Table, Timeline (Gantt) and People views**, sharing the same search and filters.
- **Timeline:**
  - bars coloured by status;
  - dependency arrows, red when a dependency is broken;
  - weekends and holidays shaded, and a line for today;
  - Day / Week / Month zoom, Fit, Today, Export PNG and a "?" legend, in the chart's top-left corner;
  - dragging a bar opens a preview of the date shift, and a grip on a bar's left edge changes only its start.
- **People view** shows a lane per person, with their milestones and dashboard work, their approved leave, and warnings about work booked over leave or over-booked days. It is read-only. The Owner filter applies to dashboard work too, and clicking a warning scrolls to the person's lane.
- **Add, Edit and Delete milestones**, with owner, department and effort. The dialog checks as you type. **Delete** can be undone. People without edit rights can open a milestone read-only.
- **Shift dates** shows a preview first, in working or calendar days. Completed work stays where it is. It can be undone.
- **Change status** records the completion date and can be undone.
- Scheduling is read-only for people without edit rights.

**Bidding**
- **Bid editor** with line items: department, complexity, reel, shots, days per shot, rate and notes. Totals update live: artist days, cost, margin, price, discount, subtotal, tax and total.
  - A discount bigger than the margin shows a **below cost** warning, and Save asks first.
  - Enter moves to the next field and never saves the bid. Long bids (240 lines) stay quick when you move or remove lines.
- **Currency per bid:** ₹ INR by default; USD, EUR or GBP for foreign clients. Changing a draft's currency asks whether to use the studio rate on every line, instead of turning ₹8,000 a day into $8,000.
- **Revisions:** sent, won and lost bids open read-only, with **Create new revision** and **Compare revisions**. A revised Won bid stays Won until the new revision is decided.
- **Decisions:** Mark sent, Won and Lost, recorded with who decided and when. Nobody decides their own bid except Admin and Developer.
- **Tracking** for a won bid compares bid, planned, delivered and actual days, and money, per department. Over-bid is shown in red. "Burned" is now called **Delivered %**, omitted shots are left out, and departments with nothing planned no longer show a green negative.
- **Create shots on the dashboard** from a won bid. A line without a reel matches the shot of that name in its one reel, instead of making a reel-less copy.
- **Export** the bid list (CSV/Excel) or a bid as a PDF. The PDF uses department names, is titled with the studio name (new in Bidding settings), and says how long it is valid.
- **Read-only Bidding** for people without the new **Edit bids** right.
- **Archive with Undo**, Duplicate to another project, and **Bidding settings** for day rates, complexities, margins and GST.
- The pipeline card counts only the newest open bid per project, with totals per currency.

#### Attendance and Leave

**Attendance**
- **Second sessions:** punch in again after punching out on the same day. All hours add up every session.
- Only the next valid button is shown (Punch in, Punch out, or Punch in again). Punching out asks first.
- **Look at any month**, not just the current one.
- **People with a team get two tabs**, "My month" and the team, and Slate remembers which one you used last.
- **Team grid:**
  - a frozen name column;
  - search, and filters for department, manager and location;
  - leave, absent and missing-punch counts;
  - holidays shown by name;
  - corrected and auto-closed days marked;
  - today underlined and days off dimmed in the header.
- **Supervisors** see their whole team's grid (everyone under them), read-only.
- **One set of rules** for the hero cards, your table, the team grid and the export: the same late count, and nobody is marked absent before they joined or after their last day.
- **Edit punch** uses time pickers, needs a reason, has **Ends next day** for overnight shifts and **Clear day**. Every correction keeps who, why, when and the old times. A day with two sessions shows one row per session (**Add session** / **Remove**), so the break is never counted as work.
- **Approved leave and location holidays** appear in attendance. Punching in on a day of approved leave asks first.
- **Export** uses the same rules as the screen, including overnight hours and open sessions. Auto-closed and corrected days have their own codes.
- **Biometric import** pairs a late IN with the next morning's OUT as one overnight day. It lists every failed day and skipped line, and matches unknown codes with a searchable person picker. Machine times replace an automatic or missing punch-out, and days outside someone's employment are listed and left out unless you choose to import them.
- Attendance refreshes itself when punches change, and search no longer re-reads the whole month on every key.

**Leave**
- **Approvers see the balance after the request**, plus the working and sandwich days, both notes, and who else from the team is away.
- **People see names, not usernames**, and you can search by name.
- **Withdraw approved leave** that has not started. HR agrees or refuses, and comp-off days come back.
- **The rejection reason is shown** to the person who asked.
- Half days say which half (first or second).
- **Requests with nobody able to approve them** (no manager, or a manager who is an artist or in HR) go straight to HR, with the reason recorded. HR can decide a request that is stuck with a supervisor.
- **The balance holds leave booked for later.** Leave years nobody closed get the carry-forward cap applied automatically.
- **Approving leave that leaves someone overdrawn** names who and by how much. Final approval of Comp Off checks the comp-off actually left for that date.
- **Requests stuck with a supervisor who cannot act** (for example, one who was deactivated) show in HR's "Waiting on me", marked "No supervisor can act – yours to decide".
- **Grant project rest** shows how many working days it gives. Revoking leave that has started warns that those days will show as absent.
- Year end shows names, a status per person, and search. It leaves out service accounts and people who had already left. Closed years show the figures that were recorded, and say so if the live figure has changed since.
- A holiday cannot be added twice for the same place by changing the letter case.

#### Joining & Leaving, and Users & Roles

**Joining & Leaving**
- One row per person and direction (joining or leaving), shown by name with the date. "Your tasks" and a **Done by** column.
- **Leaving adds a "Return <machine>" line for every machine** the person holds.
- HR can cancel a checklist started by mistake.
- The person picker searches by name and refuses text that matches nobody.
- Admins, and people who are both HR and IT, get both halves of the screen.
- **A re-hire gets a fresh joining checklist.** An open checklist is topped up, never doubled.
- **Start leaving** also offers people who have already left, marked "(left)", so their kit is still chased. Any machine held by someone deactivated or past their last day counts as not returned.
- Starting a checklist keeps the person's employment type and department (it used to reset them to Staff). A last day before the joining date is refused, and a past last day asks first, because it switches the account off.

**Users & Roles**
- **Deactivate / Reactivate** accounts. Deactivated people are hidden unless you tick **Show deactivated**.
  - **Deactivate** says how many people report to the account and how many leave requests wait on them, and offers to move the team to a new manager.
  - **Reactivate** offers to clear a last day that has passed. **Edit user** has a clearable **Last working day**.
  - Editing your own account warns you before you remove a right that would lock you out, and the last active Developer cannot be removed.
- **Add user** refuses a username that is already taken, while you type.
- Reports to, Employment, Joined and Location can be cleared. **Reports to** only offers people who approve leave (never a service account), and refuses loops. Import applies the same rule.
- **Export** of the user list includes status and last day.
- Search across every column, a Location column, and sortable tables.
- **Roles & Permissions:**
  - ticks wait for **Save changes / Revert**;
  - **Rename role**;
  - role changes appear in the audit trail with screen names.
- **User import:**
  - reads Department, Joined, Reports To, Location, Employment and Role;
  - the role list starts empty instead of Admin;
  - imports run in the background;
  - **Update existing people** is opt-in and shows a preview first.

#### IT Support, Hardware, Deployment and Licences

**IT Support**
- **SLA clocks count working hours:** 10:00–19:00 on the studio's working days, with the weekly offs from Studio policy and studio holidays skipped. P1 counts around the clock. The hours can be changed in Settings.
- A ticket waiting on the requester is **Paused**, not Breached. The SLA cell reads "3 h left to fix", resolved tickets record whether the promise was kept, and there is a monthly SLA report. Durations never round up to a full day that is not there.
- **IT can:**
  - assign a ticket to a colleague, or unassign it;
  - change the priority, with a reason. Raising it restarts the promise from now;
  - keep internal notes;
  - raise a ticket on someone's behalf;
  - assign, set status and change priority from inside the ticket, not only from the queue.
  - Resolving or closing a ticket needs a note, and so does **Waiting on requester** (what IT need from them). Setting a status counts as the first response.
- **Requesters can** confirm a fix, withdraw or reopen a ticket (Reopen asks what is still wrong), and see their ticket number after sending.
- **Every line of a ticket's conversation wraps**, so a long path in a note no longer widens the window.
- **Notifications** in the header bell: new P1 and P2 tickets, tickets raised to P1 or P2, and requesters' replies for IT; pick-up, replies, "Waiting on you" (with IT's question), priority changes and Resolved for the requester.
- Clickable figures (Unresolved, Breached, At risk, Unassigned, Mine), filters, and search by #number or name.

**Hardware**
- **Status follows who has the machine.** New machines start as Available.
- New end-of-life states: Retired, Lost and Disposed.
- **History** of every loan, **Rename** (which carries the history with it), and warranty dates, with expired or soon-to-expire warranties highlighted.
- New details: type, serial number, asset tag, and RAM and storage in GB. Serial number and asset tag can be shown as columns and are searched.
- **Who has a machine comes from the loan record only.** An old typed-in owner shows as a note, not as "Assigned to".
- **Issue to…** uses the person picker and asks for the issue date. Issuing to someone who is leaving explains why it is refused and offers **Issue anyway**.
- Status changes and renames are kept in the machine's history. **Sync** names retired machines that have reported in again, and **Delete** explains why it is off for a machine that was ever issued.
- Search, sorting, and clickable figures.

**Deployment**
- Record a deployment for **several machines at once**, with version and notes. Retired machines are not offered, and typing one asks first.
- Records can be edited and deleted. Mark success or Mark failed records who and when, and Mark failed asks why. Cancelling the "why" box marks nothing, and both marks can be undone.

**Licences**
- **Readings panel** per licence: a chart against seats bought, with Correct and Delete.
- **Import from licence server:** load a saved `lmstat -a` or `rlmstat -a` text file. Nothing is contacted over the network. Only exact product names are ticked, adding up several products asks first, and importing the same file twice does not double the readings.
- **Annual cost** (INR by default), vendor, contract/PO and notes, and the cost of spare seats.
- Perpetual licences ("No expiry").
- **"Not measured lately"** shows when the last reading is older than the window, with its age.
- **Renewal reminders** go to IT and to people who can view Licences, through the bell, at the studio's warning window (45 days by default), then at 14 days and at expiry. They are sent in the background while Slate is open, not only when someone opens Licences. Home shows **Licences to renew**.
- People who may only view Licences see the screen read-only.

**All four screens** have an **Export** button (CSV/Excel of what is shown) and show people by name.

#### Admin Panel, Live Ops, Data Center and Settings

**Live Ops**
- Machines that stop reporting turn **Not responding**, then **Offline** with when they were last seen. Before, they stayed green or vanished. This is judged by the shared folder's clock, so a workstation with a wrong clock cannot look online.
- A summary strip, search, status filter and sorting. Problem machines come first.
- Disk use shows as "C: 81% full", in amber from 80% and red from 90%.
- **Fleet report** (CSV/Excel/JSON) has readable headers and totals that add up, and numbers are real numbers. Disk figures are refreshed every five minutes.
- If the shared folder cannot be read for a moment, the cards stay and a line says when the last good read was.
- **Restart** and **Shut down** confirm that the request was sent. Admins can **Remove from Live Ops** a machine that is gone for good.

**Audit Logs**
- **Workstation logs** can be read in every format Slate writes, with level and source split out, filters, and a detail pane for errors.
- **Change history** names the shot or item each change was made to, with a date range, Load more, and CSV/Excel export.
- **Audit trail** is a new tab for user and role changes, attendance, onboarding, renames and admin actions, with a date range and Export. It says when it shows only the newest 5,000 entries.
- Pages reload only when you come back after two minutes, not on every switch.

**Data Center**
- Edits and deletes use each table's real key. A deleted row is named before you confirm, and edits and deletes are logged with who made them.
- **Purge stock library** moved into a Data maintenance dialog where you type `PURGE`.
- The overview counts only active users and has a readable role chart.

**Tester Panel**
- One shared test folder. The panel works in a `Slate_tester` folder inside the folder you pick, and never takes over a folder that already holds anything. Destructive tools only act on folders the panel made itself, and are for Developers.
- **Workflow simulation** shows its total size first, keeps 10% of the disk free, asks above 5 GB, and is for Developers above 1 GB. A stopped run says it stopped early.
- **VACUUM and Integrity check** work on PostgreSQL.
- The regression run reports real pass/fail numbers. Folder comparison matches files by their path, not only by name.
- The config sandbox is gone (see *Upgrading*).

**Settings**
- **One Save / Discard / Reset to defaults** bar, with an "Unsaved changes" marker. One Save gives one message.
- **Theme:** Dark or Light.
- UI scale: Auto, or 0.75 to 1.50.
- **Test connection** for the database.
- **Studio policy** and **Studio currency, rates and hours** cards apply to the whole studio. Artists see preferences and a read-only policy summary.
  - Weekly offs in Studio policy are the studio's one working week. The currency, rates and hours card keeps only the start and end of the working day.
  - That card is split by right: currency, day rates and GST for people who approve bids; working hours and the licence warning for admins, developers and IT.
  - The auto punch-out time must be at least 4 hours after the late cut-off, and the card shows how long a forgotten day would be.
- Clearing the server folder is refused ("Slate needs the studio folder") instead of saying "Saved" and keeping the old one.
- "Back up a project" is removed (see *Upgrading*).
- Studio logo: it now sits in its own box beside the SLATE wordmark, instead of over it.

#### Light theme

- **Settings → Theme → Light.** Every screen takes its colours from the theme: header, sidebar, tables, dialogs and all the tabs. Light, Dark and the old "Slate" name (the same as Dark) all work.
- **Dark stays the default.** Nobody's look changes on upgrade.
- Menus, dialogs and new windows switch at once. Screens that are already open switch when you next start Slate.
- In every theme, video and image pictures are shown on black (a neutral surround for judging colour, as in RV or Nuke), and Home's background stays dark.
- **Shared look everywhere:** one height for all fields and buttons, chevron arrows on drop-downs, visible checkboxes (including tick boxes inside tables and lists), and one table style with a clear selection. Icon buttons look disabled when they are.
- In dialogs, **Enter presses the main button and never Cancel**.

### Fixed in this release

#### Sign-in & window
- Sign-in messages were cut to one line, with letters missing at both ends.
- A password saved with a space at the end, or made only of spaces, locked the account. Spaces at the start and end are now ignored everywhere a password is typed, and a password of only spaces is refused. A password saved with spaces before this still works when typed exactly.
- **About 3 seconds after start-up, Slate jumped back to Home**, undoing the first screen you opened.
- **Closing Slate or signing out punched you out of attendance.**
- The header **Sync** button did nothing.
- The latency dot ran a database check on the main screen every 5 seconds, which could freeze Slate.
- Ctrl+1 did nothing, and Ctrl+N opened screen N−1.
- The command palette listed headings such as "Go to Tab: __HEADER__PRODUCTION", and missed screens in folded groups.
- The sidebar folded the Production group away whenever you opened a lower tab.
- The window refused to be shorter than 768 px, which is taller than a 1366×768 laptop screen. Help opened at 1180×800.
- Signing out of Slate VFX or Slate Operations reopened the all-in-one window.
- The automatic update check ran, but its result was thrown away.
- Labels lost their `&`: the Build & Ingest button read "Build_Move Files".
- Settings paths with `&` or an apostrophe got more garbled on every restart.
- **Closing the Slate Server window stopped the studio database** without asking.
- **With the database down and the local fallback switched off, the sign-in window never opened:** about 3.5 minutes of nothing, then a crash. With the database service stopped, Slate showed nothing for about 30 seconds.
- **An RV verdict was never saved**, and the message blamed your permissions. It is now a pending change like any other dashboard edit.
- Jumping to a shot from the command palette failed on a fresh start ("No shots are loaded").
- Notifications could not be opened. If they could not be read, the bell said "No notifications yet".
- When a screen failed to build, the sidebar highlighted it while the previous screen stayed on show.
- The header checked the shared folder on the main screen every 5 seconds.
- **Slate Server window:** **Restart Pool** worked while the database was off, and **Apply Update** stopped the database with a plain Yes/No.

#### Home
- "Leave Management" and "IT Ticketing" tiles did nothing. Tiles appeared for screens you could not open.
- "My recent tasks" showed the first five shots in the database, for everybody.
- With the database down, Home stayed on "Loading…" forever. Without graphics acceleration it stuck on "INITIALIZING SINGULARITY".
- The animated background drew every frame forever, even behind the panels.
- Figures were cut in half on 768-pixel-high screens.
- **Clicking a shot on Home opened an empty dashboard**, and **See all my shots** landed on "No project selected".
- **Every artist saw everybody's leave** on Home, with the leave type and status.
- "Leave to decide" counted every pending request in the studio. "Shots waiting for review" counted archived projects.
- When working offline, between midnight and 05:30 Home read yesterday's punch and offered Punch In again.
- Punch Out acted on one click, and the 60-second refresh took keyboard focus off the row you were on.

#### VFX Dashboard
- **Opening a cell editor and clicking away erased the cell.** A blank status became WIP.
- **Saving from the detail panel wiped department statuses**, invented target dates and reset type and status.
- **Save was blocked by a false conflict**, and department rows were never saved, when a shot name existed in two reels. The board and artist assignment could also hit the wrong shot.
- **Closing Slate or signing out discarded unsaved grid edits** without asking.
- Every save rewrote all shots in the project, even for one edit. Force overwrite did the same.
- Edits saved with Save Changes wrote no history and sent no notifications. History showed "Unknown" and "None" for every row.
- Board column counts were wrong, and the board used statuses the rest of Slate did not know.
- Changing status or artist on the board also overwrote the Comp department.
- **Rights:**
  - leads could change shot status and assignment through the board;
  - anyone with the dashboard could approve versions from the Review Queue;
  - artists could create delivery packages;
  - Compositors and Roto Artists saw the whole project.
- Search ignored most columns. Filters carried over to the next project. Column widths and "Reset to Default Layout" never stuck.
- "Save as Project Default" always failed. Space did not open Quick Look.
- Invalid numbers silently became 0, so "High" priority turned into Urgent. Target dates sorted as text.
- Omitted shots counted as outstanding and overdue. The production summary silently followed the current filter.
- The offline banner was red text on red. The toolbar overlapped itself on laptops. A row with pending edits was painted black.
- Notifications went to the person who made the change, and some said "is now " with no status.
- Placeholder thumbnail paths from a developer's machine were saved into shots and Excel.
- Reports → Delivery batches crashed.
- **Shot panel edits were silently lost** when someone else saved the open shot while you typed. Closing the panel threw away unapplied changes without asking.
- **Filters on the pinned Reel and Shot Name columns did nothing**, and survived Clear.
- **Shot history recorded only status and artist changes.**
- **Every save wrote 0 "actual days"** for every department, so Bidding thought days had been recorded. An artist setting their own status wiped the shot's bid days.
- **Clearing a shot's Artist never stuck**: it came back from the Comp department on reload.
- **A permanently deleted project came back** as soon as anyone who still had it open saved. Creating a project with an archived project's code silently brought the old one back and overwrote its settings.
- An artist's Undo could not put back a status they were not allowed to pick, and after switching project it acted on the other project's shot with the same name.
- A lead's save was refused as a permission breach when someone else had only changed another field. Refresh reported "Someone else changed" shots nobody had touched.
- A "Paint Lead" was put in the AI department, because department names were matched inside other words.
- **Review:** verdicts in the Review queue never touched the shot. Notes were all signed "supervisor", were read as HTML, and could not be added anywhere. Versions ignored the reel. Leads could approve any department's versions. Unsent versions filled the queue.
- **Artists could read every version, artist and client note of the project** in the Review queue and Delivery batches.
- **Excel backup** dropped targets, In/OS, edit status, description, previous version and actual days. One pasted control character made every backup fail while the toolbar still said "Backup saved".
- **Auto-publish** flattened every version into one folder. A delivery that failed half-way stayed in the database as a partial package.
- A failed read showed the project as empty and said "Loaded".
- Bid days left and artist load still counted departments of shots that were already approved or delivered.
- "Add new project" asked for the studio's tracker sheet, never read it, and then wrote backup rows into it in the wrong columns.
- "Open in" walked every file under the shot's folders before it launched anything.

#### Build & Ingest and CAP Rename
- **Choosing or saving a pipeline template crashed Slate.**
- **A stopped ingest was reported as a clean success.**
- **Files with destination paths over 260 characters always failed.**
- Plates named with mixed separators made a fake sequence and a false "short delivery" of 2,006 frames.
- There was no confirmation before moving the whole client drive, and no copy-only mode.
- Dry run wrote into the target. The run summary vanished the moment a run ended. Pause and Stop waited for the current sequence to finish, and "Paused" was never shown.
- The progress bar and buttons were off-screen at 1600×900 and below.
- Loose files became a "shot" named after the drive. Nested folders picked the wrong reel. Rescans (ScanA / ScanB) were offered as stitches. Target Reel merged shots from different reels.
- Saving a template called "Standard" said it worked, but saved nothing.
- **CAP Rename never renamed anything.** It failed on the first file for everybody, and left an empty undo file behind each time.
- CAP Rename's undo script could destroy files when names were swapped. Sanitize removed the file extension and wiped non-ASCII names. Any name containing "ERROR" was refused.
- The CAP Rename preview shrank to two rows on a laptop, and its conflict colours did not show.
- **Plates in per-format sub-folders (`SH_010/EXR`, `SH_010/MOV`) became shots called "EXR" and "MOV"**, and different shots were merged.
- **Plates more than 6 folders deep were silently left behind**, and the report called their folder "empty".
- **LUT, reference and audio folders became shots** and were added to the Dashboard.
- A stopped run could not be resumed: running again copied the whole shot into a new scan version. Retry never added shots to the Dashboard, skipped the Denoise folder, and left "Finished with problems" on screen after it succeeded.
- When a drive held two deliveries of a shot, the Dashboard recorded the older one.
- With a template whose client folder had another name, documents were offered again on every run and an extra `01_Frm Client` folder was made. Emptying "Inside each scan version" brought back a Denoise folder.
- **Custom templates were saved on one workstation only.**
- A run paused for more than 6 hours lost its lock, so a second ingest could write to the same project. Only admins could clear a coordinator's own leftover lock.
- Move said the client drive was emptied but left the folder tree behind. A dry run wrote to the database.
- Project codes with spaces, emoji or 150 characters were accepted, and an existing `My-Show` folder was not noticed next to a new code `MYSHOW`.
- Shot folders named in Hindi became a bare number and clashed.
- The delivery report counted frames that never arrived.
- **CAP Rename:** one file that could not be renamed left a hole in the new sequence. Undo of 3,000 files froze Slate for 10 seconds. Anyone on the same Windows login could undo someone else's rename. A partly failed rename was logged as a success. Sanitize turned `plate.1001.exr` into `plate_1001.exr`.

#### Stock Viewer and Timeline Viewer
- **Tags and metadata from ingest were never saved.** Every asset stayed "Pending" with no metadata.
- **Imported tags were split into single letters.**
- Still images (JPG/PNG) showed "Playback Error". Numbered stills were merged into fake sequences.
- **Sort did nothing. Every Visual filter showed "No Matching Assets".** The category filter and the "of Y" total only counted rows already loaded.
- Dropping several folders only ingested the first. Re-ingesting added duplicate cards.
- Favourites was always empty, because nothing could mark a favourite.
- Proxies were forced to 1920×1080 with cropping, and non-16:9 clips were stretched.
- Clear Library left every thumbnail and proxy on disk.
- `_` and `%` in search behaved as wildcards.
- Ingest took about 1 second per small image, even in Fast mode.
- The play button showed paused while playing. Image sequences froze Slate while they were probed.
- Switching clips quickly could crash Slate.
- **RV playlists never opened.** The session file Slate wrote was rejected by RV, so the dashboard's "Open 2 in RV" failed. It is now the format RV writes itself, checked with RV.
- **A re-rendered shot kept playing its old proxy**, and Make review proxies called it "already there". A proxy older than its render is now passed over and offered to be made again. **Rebuild all** did not rebuild anything; it does now.
- Timeline shots were ordered by the last number only, mixing reels. DMP, CG, Roto, Matchmove and Slapcomp renders never appeared.
- **Deleted stock assets came back** on the next ingest or Rescan.
- **The inspector kept showing (and could keep playing) an asset** that was no longer selected or no longer listed.
- Automatic categories matched parts of words ("december_snowfall" was Sparks), and automatic tags were full of filler words.
- **Ingested EXRs were previewed from an 8-bit JPG**, so the colour controls never appeared. Quick Look played clips without sound.
- After playing and pausing, the first step back did nothing, and a snapshot saved the next frame instead of the one on screen. Playing to the end showed a frame past the end ("148 / 144").
- A file that could not be read showed "Analysing…" forever and was re-analysed on every Rescan. Unplayable files showed ffmpeg's raw error. Sound files were skipped without a word.
- Import counted assets already there as "Imported". Export carried the exporter's favourites, database ids and cache paths.
- The grid sometimes laid out one column short, and sorting by a column header left the Sort box showing the old order.
- The Timeline Viewer listed every shot at 24 fps and every movie plate as "~100 (length unknown)". Ticking a shot threw the player back to the first shot.
- A metadata check finishing after a clip was closed could crash Slate.

#### Scheduling and Bidding
- **Saving a milestone or a bid gave no confirmation and did not refresh**, so people saved again and made duplicates.
- Changing the project never refreshed "Depends On", so dependencies on other projects were saved.
- Shifting a milestone with empty dates failed silently. Shifts could stop half-way, moved completed milestones, and said "Success" when nothing moved.
- Cells could be edited in place, but nothing was saved. Milestones could not be edited or deleted.
- **One bid with an empty field stopped the Bidding tab from opening.**
- **Editing a bid re-priced it at the default day rate**, and an approved bid took the project's current shot count.
- Final budgets were rounded: cents went wrong, and whole dollars were lost on large bids. Budgets were also capped at $1,000,000,000.
- A margin of 99.99% gave a price 10,000× the cost.
- Currency was always "$", but grouped like rupees.
- The pipeline added up every draft for the same project.
- Deleting several bids only deleted the first.
- **Opening Edit on a milestone that starts before its dependency ends silently moved its start date** on Save.
- Picking a dependency that ends before a weekly off put the start on the off day. Dragging a bar said "working days" when it meant calendar days. Undo said "undone" even when it failed.
- Read-only users were told to drag bars, and could not open a milestone at all. Milestones could be saved for projects that do not exist or are archived. Archived projects' old milestones counted as Overdue.
- **"Create shots" made duplicate, reel-less copies** of shots already on the dashboard.
- **Revising a won bid took its money off the Won card** and hid its tracking until the new revision was won.
- Export as PDF failed for an older bid saved with a 100% margin. A discount larger than the margin priced the job below cost without a warning.
- "Price" meant one thing in the bid list and another in the editor.
- Pressing Enter in a line's description saved the whole bid. A bid with 240 lines froze for about 2 seconds on every move.
- A bid that had been sent and won could be deleted after "Reopen as draft".
- Changing a draft's currency kept every rate number, and switching to EUR or GBP set every rate to 0.
- Tracking counted tasks of omitted shots and missed DELIVERED shots as done.
- Anyone who could open Bidding could make and edit bids, and there was no read-only Bidding.
- The client PDF showed department keys ("comp", "roto") instead of names.

#### Attendance and Leave
- **Correcting a punch always failed on PostgreSQL.** Biometric import failed for every day that already had a record.
- The WFH flag was wiped on punch-out. Auto punch-out damaged the day's details and produced 23-hour shifts.
- **Punch Out with no punch-in said "Successfully Logged OUT"** and saved nothing. A second Punch In was ignored but reported as a success. Each Punch Out overwrote the last.
- Forgotten punch-outs showed as "Working" forever.
- An out time earlier than the in time became a 15-hour shift.
- Three different "late" counts were shown for the same person.
- The Excel export disagreed with the grid.
- Approved leave never appeared in attendance. Location holidays applied to everybody.
- **Supervisors saw and could edit the whole studio's attendance**, while Comp Supervisors and Team Leads saw none.
- **Approving a request that had been withdrawn brought it back.**
- Requests from people with no manager were stuck forever.
- Future leave was not held against the balance. Long-serving staff showed 180 days available until somebody ran year end.
- HR could give final approval to their own leave. The HR decision overwrote the supervisor's note. Rejection reasons were never shown.
- The sandwich rule could be dodged by asking for more days.
- Comp-off was judged on the day of approval, not the day of the leave, so the wrong day could be spent.
- **Correcting a day with two sessions merged them** and counted the break as worked hours.
- **Leavers were marked Absent every working day after their last day**, and the export marked new joiners Absent before they joined.
- The late count still differed between the hero card and the table, grid and export. A first-half leave day was marked Late when the person came in for the second half.
- Biometric times did not replace an automatic or missing punch-out, and import over a two-session day kept the old hours. It also wrote punches after someone's last day.
- A forgotten punch-out on a weekly off showed as worked with 0 hours. The WFH tick was cleared by the refresh before punching in. Every sign-in said "Attendance logged" even when nothing was.
- At 1280×720 the attendance tables showed one or two rows.
- **A Sunday punch-in with no punch-out earned a full comp-off day.**
- **Leave asked for inside a year HR had closed cost nothing**, and leave could be asked for before joining or after the last day.
- A half day between two days off was charged 2.5 days. Every pending request of a person showed the same "balance after".
- One long name pushed Status and Waiting on off the leave queue.

#### Joining & Leaving, and Users & Roles
- **Typing a name in Start joining could start the checklist for a different person.**
- A person joining and leaving was merged into one row with 19 tasks. "Workstation returned" was ticked when only one of several machines came back. The joining date you typed was ignored.
- **Add New User with an existing username took over that account.**
- Supervisors could create Developer accounts. HR and IT could give themselves Full access.
- Removing Full access left every box ticked.
- Reports to, Employment, Joined and Location could never be cleared. Reports to accepted anyone, including loops.
- Import gave everybody the Admin role by default, and froze Slate for about 17 s per 100 people.
- **A re-hire never got a new joining checklist.** Starting a checklist reset the person's employment to Staff. Someone whose last day had passed could not be put on the leaving list, so their kit was never chased.
- **A leaver could not be brought back**: Slate said to clear the last day on Edit User, which had no such field.
- Resetting someone's password wiped their profile picture.
- An admin could remove their own admin rights from Edit user with no warning.
- Import still accepted an artist as "Reports to", and "Reports to" offered the System Admin account.
- Deactivate said nothing about the people who report to the account.
- The exported user list had no status or last day, so deactivated people came back as active on re-import.

#### IT Support, Hardware, Deployment and Licences
- **The status filters on Hardware and Deployment did nothing.**
- **A ticket with an empty description could not be opened.** IT staff and admins could not raise tickets at all.
- SLA clocks ran on calendar hours while the promise said business days. "At risk" used the wrong clock. Setting "Waiting on you" again wiped the banked waiting time, and the requester's reply did not take the ticket off it.
- "Assign to me" silently took tickets away from colleagues.
- Pressing Enter in a ticket summary cancelled the ticket.
- Hardware said "Issued" even when the loan was not saved. New PCs started as "Active", and the status could contradict who had the machine.
- Cells were editable in Hardware and Deployment, but edits were never saved.
- A recorded deployment did not appear.
- A second licence contract for the same product took the other contract's usage. Readings of a removed licence came back on its replacement. 0 seats with 3 in use was "Healthy".
- Long licence names failed to save without a message. Enter cancelled the licence dialog.
- **A requester's "Thanks, works now!" reopened a resolved ticket.**
- A long word in a status note widened the whole conversation and clipped every message.
- Escalating a ticket to P1 or P2 told nobody, and an old ticket raised to P1 was breached the moment it was raised.
- One long requester name squeezed the Summary column to a few words. My tickets forgot your sort every 30 seconds.
- The SLA report counted older resolved tickets as missed.
- **A machine with an old typed-in owner showed "Assigned to"** while the loan record said nobody had it. The figures double-counted a machine in repair that was still on loan.
- **Licence import added render and other feature counts into the main licence** (Nuke read 18 of 10). A licence with only old readings said "No usage has been recorded".
- **Cancelling the "Why did it fail?" box still marked the install Failed.** Only the first machine in a list could be marked complete.
- Having the IT tab alone gave full write on Hardware and Deployment.

#### Admin Panel, Live Ops, Data Center and Settings
- **A machine that stopped reporting stayed green "Online" forever**, and offline machines vanished.
- The change log showed "Unknown" for every user. Changes never said which shot. The audit trail was never shown.
- The log table jumped back every 5 seconds while you read it. Errors were shown as green INFO.
- A refused Data Center edit stayed in the cell without a message. Rows were keyed by guesswork.
- Purge emptied the stock library and its linked tables after one Yes/No.
- Tester Panel: cancelling Browse emptied the target. VACUUM and Integrity check always failed on PostgreSQL. The regression suite always said "Suite Completed!".
- The Settings **Dark Mode switch cycled three themes**, and Light changed nothing.
- Artists saw studio policy, the database connection and the server root in Settings.
- Studio policy was stored per machine, so machines could disagree.
- Unsaved Settings edits vanished silently.
- A studio logo was painted over the SLATE wordmark.
- **A moment's trouble reading the shared folder wiped every Live Ops card** and said "No workstations have reported yet". A workstation whose clock ran fast stayed Online after it stopped reporting. The freshness dot beside each machine was invisible.
- Problem machines were sorted to the bottom. Export PDF always said "Saved", even when the file was not written.
- **Tester Panel:** any folder became "made by the Tester Panel" after one generator run, and could then be deleted with everything in it. The Workflow simulation could write about 500 GB with no space check and no question. The config sandbox's banner claimed Live Ops used the new folder when it did not.
- **The studio's working week was stored twice**, and the two could disagree. IT saw the currency, rates and hours card but could not edit even its own working hours.
- Clearing the server folder said "Saved" but kept the old one. The policy accepted an auto punch-out time before the start of the day.
- "Simulate crash" said Slate would close, but Slate keeps running.

#### Light theme
- Choosing Light changed almost nothing: the old dark stylesheet was laid over every window. Every screen now follows the theme.
- In Light: YTS and OMIT pills, Home's **Try again** button, CAP Rename's "Will rename" names and the toggle switches were hard or impossible to see. In Dark: unticked boxes in tables (such as the RV picker) were nearly invisible.

### Known limits

**Whole window**
- **Changing the theme** applies to menus, dialogs and new windows at once. Screens already open change when you restart Slate.
- On a 1600×900 screen an admin's **expanded sidebar** still scrolls a little (about 58 px), so the last entries sit just below the fold. The icon-only rail fits.
- Long shot names on Home are shortened to a fixed width, not to the space actually free.
- The **Slate Server window** cannot be made narrower than 1120 px, so that Analytics and Operations fit. The Analytics cards do not rearrange themselves for narrow windows.
- **Check for updates** in Settings always shows a found update, even after "Remind me later". This is deliberate, since you asked.

**VFX Dashboard**
- **Opening a big project still loads on the main screen**, so Slate can pause for a moment. Filtering, the board and thumbnails no longer cause pauses, and 262 shots open fine.
- **Saving many shots still runs on the main screen.** It is much lighter than before (only changed department rows, written in batches, one project read), but a very large save can pause Slate briefly.
- **The Excel backup keeps existing rows where they are.** New rows go in reel/shot order, but Slate does not re-sort a studio's existing sheet.
- The **Coordinator** role does not get "Force-save over others" by default. Tick it on the role if your studio wants it.
- Older shot history for a shot name that exists in two reels cannot always be tied to the right reel.
- The unread-feedback dots are remembered on each machine, not per person across machines.

**Build & Ingest, CAP Rename and media**
- Another person's ingest lock that is still fresh can only be cleared by an admin.
- Shot names in non-Latin scripts are spelled out from the letters' names and marked "check it". This is not a real transliteration, so check them before you ingest.
- CAP Rename reorders with **Move up / Move down** and sorting. Rows cannot be dragged.
- **Review proxies stay in a `proxy` folder next to the frames** (for plates, inside the scan version, for example `01_Scan\v001\EXR\proxy`). Help says exactly where. Stock proxies are kept apart, in the server's `Cache` folder.
- Image sequences play, and are timed in EDLs, at 24 fps, because projects have no frame-rate setting yet. Movie plates use their own rate.
- Importing an exported stock library brings back files that were deleted.

**Scheduling and Bidding**
- The Scheduling **People view is read-only** in this version.
- On a 1280×720 screen the **Timeline** gets only a little more height than before (its controls moved into the chart's corner). The cards and filters above it are unchanged.
- **Only studio-wide holidays** (place "All") count as non-working days in Scheduling. Holidays of a single office are left out, because projects have no office yet.
- When no studio name is set, a bid PDF is titled "Bid CODE vN". Set the studio name in **Bidding settings**. A PDF is always valid for 30 days.

**People**
- There is no one-click "approve as Unpaid". When approving would overdraw someone, Slate says so and suggests rejecting and asking for Unpaid.
- The comp-off minimum is always half the standard day. It follows the standard day in Studio policy, but cannot be set on its own.
- An `artist` account created by an older version is not a service account, so Users & Roles still flags its missing joining date. Delete it if nobody uses it.
- Biometric files only pair a late IN with the next morning's OUT when the machine records whether each punch is an IN or an OUT.

**IT**
- If a ticket is lowered from P1 to P2–P4 while it has waiting time banked, that part keeps the clock it started with. (Raising a priority restarts the clock.)
- Working hours cannot run overnight (for example 22:00–06:00). Settings refuses them.
- Licence renewal reminders are sent from the bell's check, at most once an hour, so they need someone to have Slate open.

**Admin Panel**
- **Audit Logs:** the change-history list still loads on the main screen. It is limited in size and now reloads only when you come back after two minutes.
- **Tester Panel Workflow simulation:** runs up to 1 GB are open to testers; larger runs need a Developer. Every run keeps 10% of the disk free and asks above 5 GB.

**When Slate works offline (local mode):**
- tickets and deployments recorded offline before this version keep the time they were saved with, which may be UTC.
