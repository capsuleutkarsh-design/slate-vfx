# Slate BETA 2.1.0

Three installers, one per machine role. Install the server first, then the workstations.

| Installer | Install on | What it holds |
|---|---|---|
| `setup_Slate Server_vBETA 2.1.0.exe` | the one machine that runs the studio database | Slate Server, PostgreSQL 17, PgBouncer, the updater |
| `setup_Slate_Studio_vBETA 2.1.0.exe` | artist, lead and supervisor workstations | Slate Studio, ffmpeg, Olive, OpenRV, the updater |
| `setup_Slate_Ops_vBETA 2.1.0.exe` | HR, IT and production office machines | Slate Operations, ffmpeg, Olive, the updater |

Nothing needs an internet connection. Every dependency is inside the installer, and updates are picked up from the studio's shared folder.

This is a big release. Every screen was gone through, as an artist, a lead, HR, IT and an admin, at laptop and desktop sizes. More than a thousand problems were found and fixed, and the features some screens promised but never had were built.

## Highlights

- **A real Light theme.** Settings → Theme → Dark or Light. Dark is still the default and looks as it always has.
- **One way to save on the VFX Dashboard.** Every edit waits as a pending change you can undo. One **Save N changes** button saves them, and Slate asks before you close with unsaved edits.
- **Scheduling has a Gantt timeline** and a People view. Milestones can now be edited, deleted and given owners.
- **Bidding has line items**, revisions, Won/Lost decisions, cost tracking against the dashboard, and PDF/Excel export. Money is in **₹ INR** by default.
- **Build & Ingest copies by default.** Move is an option. A check runs before every ingest so you see what will happen first.
- **CAP Rename works again**, with a real **Undo last rename**.
- **Stock Viewer**: favourites, Studio picks, sound in previews, search by "4K" or "24fps", and Undo after delete.
- **Timeline Viewer** has its own preview player, so you can play the lineup inside Slate.
- **IT Support** clocks count working hours (Mon–Sat 10:00–19:00), so a ticket raised on Saturday evening is not "breached" on Monday morning.
- **Closing Slate or signing out never punches you out.** You can also punch in again after punching out, as a second session that day.
- **Smaller laptops work.** The window fits 1366×768 and 1280×720 screens, and pages scroll instead of being cut off.

## Upgrading from 2.0.32

**Back up the studio database before you upgrade.** This release changes the database (see below). The changes keep your data and remove nothing, and they were tested on both PostgreSQL and the local database, but one of them changes a column type on a live table. A backup is the safe way back if anything goes wrong.

1. Back up the database: **Slate Server → Operations → Back up now** (see "Backing up and restoring the database" in the README).
2. Run the new **Server** installer over the old one.
3. Start **one** workstation (Studio or Ops) first and sign in. The database changes are made by the first Slate that starts against the upgraded database, and that first start can take a little longer than usual. Use an HR or admin machine if you can (see *Studio-wide settings* below).
4. Then install on the other workstations. Settings and data are kept.

### What changes in the database

All changes add things. No table is removed.

- **Scheduling dates become real dates.** On PostgreSQL, milestone start and end change from text to `DATE`. Every row is rewritten in one date format first. Any date that cannot be read is kept in a new `legacy_dates` column instead of being lost. Links to milestones that no longer exist are cleared.
- **New tables:**
  - bid line items;
  - studio-wide settings;
  - a record of the one-time database repairs;
  - stock favourites, Studio picks and stock ingest folders;
  - licence renewal reminders;
  - comp-off spends, so comp-off days come back when leave is cancelled.
- **New columns:**
  - **bids:** currency, client, day rate, discount, tax, revisions, notes, archive, and who sent or decided them and when. Bid amounts are stored as exact money (`NUMERIC`) on PostgreSQL.
  - **milestones:** owner, department, effort and completion date.
  - **shot tasks:** "actual days" per department.
  - **shot change history:** which shot, reel and department each change was made to.
  - **leave requests:** separate supervisor and HR notes, which half of a half day, and withdrawal details.
  - **joining/leaving lines:** who ticked them and when.
  - **user accounts:** active / deactivated, and a service-account flag (the `admin` and `tester` accounts get it).
  - **IT:** licence cost, vendor, contract and notes; machine type, serial number, asset tag, purchase and warranty dates.
  - **stock library:** category, visual tags, sequence frame range, who added it, and soft delete.
- **Attendance day details** become a proper JSON column on PostgreSQL. Rows that the old code had glued together are merged back first.
- **Notification times** keep their seconds on PostgreSQL (the column is widened, values kept).
- **The old IT licence table** is copied once into the current licence list (name, seats and expiry). The old table is kept and marked legacy.

### One-time repairs, made on the first start

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
- notifications that were stored under a display name.

On each machine, a settings file damaged by the old `&` / apostrophe bug is also repaired once.

### Studio-wide settings

- **Studio policy now lives in the database, so every workstation uses the same rules.** It covers the late cut-off, the standard day, the auto punch-out time, weekly offs, accrual, carry-forward, the sandwich rule, comp-off and project rest. Before, each machine had its own copy.
  - The **first** machine to start after the upgrade gives its old values to the whole studio. Check them afterwards in **Settings → Studio policy**.
  - HR and admins can change the policy. Everyone else sees it read-only.
- **Currency is ₹ INR by default**, with Indian digit grouping (₹2,07,00,000.00, or ₹2.07 Cr in short form).
  - **Existing bids keep their numbers and are marked USD**, because the old screens showed "$".
  - New rupee bids get 18% GST by default, which you can change per bid. USD, EUR and GBP bids start with no tax.
  - Day rates start at ₹8,000 / $300 a day. Admins can change the currency, day rates, GST, working hours and licence renewal warning in **Settings → Studio currency, rates and hours**.

### Roles and permissions

- **Roles only gain rights on upgrade.** Once, on the first start:
  - roles that open Scheduling may now edit it;
  - roles that open Bidding may now decide bids;
  - artist-type roles can be given shots;
  - supervisors, coordinators, producers and heads see every shot on the dashboard.
- **The Producer role gains the VFX Dashboard tab.** It also keeps Excel export now that it is no longer treated as a supervisor.
- **Some defaults changed:**
  - **Supervisors** no longer manage users, and no longer see the whole studio's attendance. They see their own reports' attendance, read-only.
  - In the **Admin Panel**, supervisors get **Live Ops, read-only**. Audit Logs, Data Center and the remote machine actions are for Admin and Developer.
  - If a studio wants supervisors to keep the old rights, an admin can tick "Manage users" or "See team attendance" on the role.
- **Coordinators are no longer treated as supervisors** on the dashboard. They cannot force a save over someone else's edit unless "Force-save over others" is ticked on their role.
- Someone who edits roles can now only hand on what they have themselves. Only Admin and Developer can give Full access.
- Full access no longer puts Developers and the admin account in the dashboard's Artist pickers.

### Behaviour you may rely on that has changed

- **Closing Slate or signing out never punches out.** The end-of-day auto punch-out closes forgotten days. After a punch-out, **Punch in** starts a second session the same day.
- **Build & Ingest copies plates by default** and leaves the client drive as it was. **Move** is still there as an option. "Overwrite Existing" is gone.
- **CAP Rename no longer writes `undo_rename_*.bat` files** next to your plates. Use **Undo last rename** in the tab. The undo record is kept in Slate's own folder.
- **Delete on the dashboard is now Archive.** An archived project is hidden, can be restored, and keeps its history. Admin and Developer also get **Delete permanently**, which asks you to type the project code.
- **Hardware:** a machine that was ever issued is **retired** instead of deleted. Retired, Lost and Disposed machines are hidden unless you tick **Show retired**.
- **Users & Roles:** **Deactivate** replaces Delete for anyone with history. A deactivated person is hidden from lists and pickers and left out of year end. Delete is only for accounts with no history. Leave stops building up after a person's last day.
- **Stock Viewer: Delete hides the asset and offers Undo.** It is removed for good after 24 hours. **Clear library** asks you to type `CLEAR`.
- **New shot names follow one rule everywhere:** Add Shots, Build & Ingest, CAP Rename's stitch names and "Create shots from bid". Names may use letters, digits, `_`, `-` and `.`, with no spaces, up to 64 characters. Shots you already have are never refused.
- **Timeline Viewer lineups are written to `<project>/editorial/lineups`.**
- **Auto-publish** fires only when a shot changes to Approved, and asks first. On new projects the output folder is `08_Deliver`.
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

## New in this release

### Sign-in & window

- **Sign-in** messages wrap and are never cut off.
  - When the database is down, a **Try again** button reconnects without restarting.
  - New: a show-password toggle and a Caps Lock hint.
  - The cursor starts in the first empty field.
- **Header:**
  - your name, job title and avatar are one account menu, with Change password, Keyboard shortcuts, About Slate and Sign out;
  - "Search or jump to… Ctrl+K" opens the command palette;
  - a long name is shortened instead of disappearing;
  - the badge says VFX or OPS;
  - **LOCAL MODE** shows whenever Slate is working offline.
- **Sidebar:**
  - groups fold only when you fold them, and stay that way next time;
  - in the icon-only rail, folded groups still show;
  - every entry has an icon and a tooltip.
- **Command palette** lists your real screens, including those in folded groups, and matches the start of words.
- **Footer:** status messages sit on the left. Running tasks show there too ("Copying plates 37%"), and you can click them to see the task list.
- **Closing, signing out or syncing** asks first if a screen has unsaved edits or a job still running, such as an ingest or a rename.
- **Messages** appear as small notes above the footer. Many have a button, such as **Undo** or **Open folder**. Error boxes say what went wrong in plain words, with **Try again** and **Copy details for IT**.
- When the database cannot be reached, screens say **"Can't reach the studio database. Your work is safe…"** with Try again. When a screen could not load, it says so, instead of showing an empty list.
- **Workspace Info** shows the version, install folder, database and server, shared folder (and whether it can be reached), settings folder and plugins, with **Copy details**.
- **Diagnostics** (Ctrl+Shift+D) shows the same facts in plain words, with a Copy button.
- **Updates:** when an update is found, a note appears with **See what's new**. "Remind me later" really waits a day, and the "check on start-up" setting is honoured.
- Starting Slate again while it is already open brings the open window to the front.
- Slate opens about twice as fast after sign-in, because screens are loaded when you first open them.
- The window remembers its size and position.
- **Help:**
  - search reads page text;
  - groups match the sidebar;
  - clearing a search returns you to the page you were on;
  - every page has been rewritten to describe what the screen really does today.
- **Slate Server window:**
  - asks before closing while the database runs, with an option to keep running in the tray;
  - the Projects and Stock Assets cards work;
  - Analytics fits smaller windows;
  - updates download in the background.

### Home

- **My recent shots** are your own shots (as lead or in any department), newest first. Click one to open it on the dashboard. **See all my shots** opens the dashboard's My shots view.
- **Figures you can act on**, depending on who you are:
  - artists see their open and in-review shots;
  - overseers see shots waiting for review;
  - operations staff see who is online, leave to decide and leave in the next two weeks;
  - IT see open tickets;
  - whoever can see Licences gets **Licences to renew**, shown in amber when something is due, with the names in the tooltip.
- **Tiles** appear only for screens you can open, and they all work, including Leave and IT Support.
- **Punch panel** shows "In since 09:04 (2 h 10 min)", **Punch in again** after a punch-out, and a **Fix a punch** link to Attendance.
- Home loads in the background. If the database is down, it says so with Try again instead of "Loading…" forever. It refreshes every minute and with F5.
- It works on 768-pixel-high screens and without graphics acceleration.

### VFX Dashboard

- **One save model:**
  - every edit (grid, board, batch edit, detail panel) is pending and can be undone with Ctrl+Z;
  - **Save N changes** saves only the shots you changed;
  - the unsaved count is always visible;
  - Slate asks before closing or signing out with unsaved edits.
- **Artists' own status changes save at once**, with an Undo note.
- **Conflicts:** when someone else saved the same shot, the conflict window shows who and when, your edits and theirs. You can **Keep theirs and re-apply mine**, or overwrite only the clashing shots.
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
  - the same rights as the grid.
- **Toolbar** folds into **More** on narrow screens and has a List | Board toggle. Shortcuts: Ctrl+S, Ctrl+F, Esc, Space (Quick Look), F2.
- **Add shots** checks names, needs a reel and refuses duplicates. **Filters** (formerly "Advanced Query Builder") only offers fields that really exist.
- **Production summary** says what it covers ("Filtered: 33 of 262") and leaves omitted shots out.
- **Shot history** shows real names and fields for that one shot.
- The project you opened last is remembered.

### Build & Ingest and CAP Rename

**Build & Ingest**
- **A check before every ingest.** The client drive is surveyed in the background, and stitches are offered first. Then a summary lists every shot, with names you can edit, Copy or Move, and warnings. Nothing starts until you confirm it.
- **Copy by default**, with checksum verification and long-path support. Move is an option.
- **Pause and Stop** act between files, and a stopped run is reported as stopped.
- **Retry failed files** runs in the background and stays available after the run.
- **Templates… menu** lets you create, edit, duplicate and delete templates.
- **The result stays on screen** after a run, with **Open report** and **Open folder**. The log is kept across runs, can show problems only, and can be saved.
- Running the same drive again copies only what is new.
- Admins can clear a stale lock left by another ingest.
- The screen fits 1280×720.

**CAP Rename**
- **Undo last rename**, which brings back swapped and shifted names exactly.
- **Add files / Add folder**, drag and drop, **Remove selected**, **Clear list**, natural sort, sort by date, and Move up / Move down.
- **Keep extension** is on by default. Sanitize keeps letters in any script (Hindi, accents).
- Every file in a name collision is marked as a conflict. A bad pattern shows its reason under Find.
- The preview shows 11 rows on a 1366×768 laptop instead of 2.
- Renames and undos are written to the studio audit log.

### Stock Viewer and Timeline Viewer

**Stock Viewer**
- **★ Favourites** for each person, plus a shared **Studio picks** list that leads and supervisors can mark.
- **Previews play with sound**, with mute and volume.
- **Search reaches resolution, frame rate, codec, category and folder names**, so "4K", "1920" or "24fps" all work.
- **Tags** can be added, edited and removed.
- **Sort and filters** work across the whole library, and the total is always right.
- **Grid or List** view, where List is a sortable table.
- The inspector shows codec, size, category and who added the asset when, with Copy path and Show in Explorer.
- **Snapshot** saves at full source resolution.
- **Quick Look** fits the screen, Space plays and Esc closes.
- **Ingest** takes several folders at once and ends with a summary. **Rescan folders** checks the folders again. Visual tags (Green Screen, Blue Screen, Warm, Cold, Dark, Bright) are rebuilt and much faster: about 70 s for 450 items, where it used to take 500 s.
- Delete can be undone. Clear library asks you to type `CLEAR`.

**Timeline Viewer**
- **A built-in preview player:** a strip of the lineup in edit order. You can choose which layer of each shot to show, and **Play lineup** plays shot after shot. Review proxies are used when they exist, and **Make proxy** creates one for a heavy EXR shot when you ask.
- **Lineup table** with include boxes, reel filter and search. Shots without a scan are greyed out.
- Shots are ordered by reel, then by every number in the name.
- Every department gets a layer once it has a render, including DMP, CG, Roto, Matchmove and Slapcomp.
- **Olive:**
  - Slate never closes an Olive you already had open;
  - only the Olive window Slate started is embedded;
  - **Back to lineup**, **Return to Olive** and **Close Olive** (which asks first).

**RV picker** shows layer, version and frames in a table, with Select all / Select none.

### Scheduling and Bidding

**Scheduling**
- **Table, Timeline (Gantt) and People views**, sharing the same search and filters.
- **Timeline:**
  - bars coloured by status;
  - dependency arrows, red when a dependency is broken;
  - weekends and holidays shaded, and a line for today;
  - Day / Week / Month zoom, and Export PNG;
  - dragging a bar opens a preview of the date shift.
- **People view** shows a lane per person, with their milestones and dashboard work, their approved leave, and warnings about work booked over leave or over-booked days. It is read-only.
- **Add, Edit and Delete milestones**, with owner, department and effort. The dialog checks as you type.
- **Shift dates** shows a preview first, in working or calendar days. Completed work stays where it is. It can be undone.
- **Change status** records the completion date and can be undone.
- Scheduling is read-only for people without edit rights.

**Bidding**
- **Bid editor** with line items: department, complexity, shots, days per shot and rate. Totals update live: artist days, cost, margin, price, discount, tax and total.
- **Currency per bid:** ₹ INR by default; USD, EUR or GBP for foreign clients.
- **Revisions:** sent, won and lost bids open read-only, with **Create new revision** and **Compare revisions**.
- **Decisions:** Mark sent, Won and Lost, recorded with who decided and when. Nobody decides their own bid except Admin and Developer.
- **Tracking** for a won bid compares bid, planned, delivered and actual days, and money, per department. Over-bid is shown in red.
- **Create shots on the dashboard** from a won bid.
- **Export** the bid list (CSV/Excel) or a bid as a PDF.
- **Archive with Undo**, Duplicate to another project, and **Bidding settings** for day rates, complexities, margins and GST.
- The pipeline card counts only the newest open bid per project, with totals per currency.

### Attendance and Leave

**Attendance**
- **Second sessions:** punch in again after punching out on the same day. All hours add up every session.
- Only the next valid button is shown (Punch in, Punch out, or Punch in again). Punching out asks first.
- **Look at any month**, not just the current one.
- **Team grid:**
  - a frozen name column;
  - search, and filters for department, manager and location;
  - leave, absent and missing-punch counts;
  - holidays shown by name;
  - corrected and auto-closed days marked.
- **Supervisors** see their own reports' grid, read-only.
- **Edit punch** uses time pickers, needs a reason, has **Ends next day** for overnight shifts and **Clear day**. Every correction keeps who, why, when and the old times.
- **Approved leave and location holidays** appear in attendance.
- **Export** uses the same rules as the screen, including overnight hours and open sessions.
- **Biometric import** pairs a late IN with the next morning's OUT as one overnight day. It lists every failed day and skipped line, and matches unknown codes with a searchable person picker.

**Leave**
- **Approvers see the balance after the request**, plus the working and sandwich days, both notes, and who else from the team is away.
- **People see names, not usernames**, and you can search by name.
- **Withdraw approved leave** that has not started. HR agrees or refuses, and comp-off days come back.
- **The rejection reason is shown** to the person who asked.
- Half days say which half (first or second).
- **Requests with nobody able to approve them** (no manager, or a manager who is an artist or in HR) go straight to HR, with the reason recorded. HR can decide a request that is stuck with a supervisor.
- **The balance holds leave booked for later.** Leave years nobody closed get the carry-forward cap applied automatically.
- Year end shows names, a status per person, and search. It leaves out service accounts and people who had already left.

### Joining & Leaving, and Users & Roles

**Joining & Leaving**
- One row per person and direction (joining or leaving), shown by name with the date. "Your tasks" and a **Done by** column.
- **Leaving adds a "Return <machine>" line for every machine** the person holds.
- HR can cancel a checklist started by mistake.
- The person picker searches by name and refuses text that matches nobody.
- Admins, and people who are both HR and IT, get both halves of the screen.

**Users & Roles**
- **Deactivate / Reactivate** accounts. Deactivated people are hidden unless you tick **Show deactivated**.
- **Add user** refuses a username that is already taken, while you type.
- Reports to, Employment, Joined and Location can be cleared. **Reports to** only offers people who approve leave, and refuses loops.
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

### IT Support, Hardware, Deployment and Licences

**IT Support**
- **SLA clocks count working hours:** Mon–Sat 10:00–19:00, with Sundays and studio holidays skipped. P1 counts around the clock. The hours can be changed in Settings.
- A ticket waiting on the requester is **Paused**, not Breached. The SLA cell reads "3 h left to fix", resolved tickets record whether the promise was kept, and there is a monthly SLA report.
- **IT can:**
  - assign a ticket to a colleague, or unassign it;
  - change the priority, with a reason;
  - keep internal notes;
  - raise a ticket on someone's behalf.
  - Resolving or closing a ticket needs a note.
- **Requesters can** confirm a fix, withdraw or reopen a ticket, and see their ticket number after sending.
- **Notifications** in the header bell: new P1 and P2 tickets and requesters' replies for IT; pick-up, replies, "Waiting on you" and Resolved for the requester.
- Clickable figures (Unresolved, Breached, At risk, Unassigned, Mine), filters, and search by #number or name.

**Hardware**
- **Status follows who has the machine.** New machines start as Available.
- New end-of-life states: Retired, Lost and Disposed.
- **History** of every loan, **Rename** (which carries the history with it), and warranty dates, with expired or soon-to-expire warranties highlighted.
- New details: type, serial number, asset tag, and RAM and storage in GB.
- **Issue to…** uses the person picker. Issuing to someone who is leaving explains why it is refused and offers **Issue anyway**.
- Search, sorting, and clickable figures.

**Deployment**
- Record a deployment for **several machines at once**, with version and notes.
- Records can be edited and deleted. Mark success or Mark failed records who and when, and Mark failed asks why.

**Licences**
- **Readings panel** per licence: a chart against seats bought, with Correct and Delete.
- **Import from licence server:** load a saved `lmstat -a` or `rlmstat -a` text file. Nothing is contacted over the network.
- **Annual cost** (INR by default), vendor, contract/PO and notes, and the cost of spare seats.
- Perpetual licences ("No expiry").
- **Renewal reminders** go to IT through the bell, at the studio's warning window (45 days by default), then at 14 days and at expiry. Home shows **Licences to renew**.
- People who may only view Licences see the screen read-only.

**All four screens** have an **Export** button (CSV/Excel of what is shown) and show people by name.

### Admin Panel, Live Ops, Data Center and Settings

**Live Ops**
- Machines that stop reporting turn **Not responding**, then **Offline** with when they were last seen. Before, they stayed green or vanished.
- A summary strip, search, status filter and sorting.
- Disk use shows as "C: 81% full", in amber from 80% and red from 90%.
- **Fleet report** (CSV/Excel/JSON) has readable headers and totals that add up. Disk figures are refreshed every five minutes.

**Audit Logs**
- **Workstation logs** can be read in every format Slate writes, with level and source split out, filters, and a detail pane for errors.
- **Change history** names the shot or item each change was made to, with a date range, Load more, and CSV/Excel export.
- **Audit trail** is a new tab for sign-ins, user and role changes, and admin actions.

**Data Center**
- Edits and deletes use each table's real key. A deleted row is named before you confirm, and edits and deletes are logged with who made them.
- **Purge stock library** moved into a Data maintenance dialog where you type `PURGE`.
- The overview counts only active users and has a readable role chart.

**Tester Panel**
- One shared test folder. Destructive tools only act on folders the panel made itself, and are for Developers.
- **VACUUM and Integrity check** work on PostgreSQL.
- The regression run reports real pass/fail numbers.

**Settings**
- **One Save / Discard / Reset to defaults** bar, with an "Unsaved changes" marker.
- **Theme:** Dark or Light.
- UI scale: Auto, or 0.75 to 1.50.
- **Test connection** for the database.
- Backup runs in the background, with **Open folder**.
- **Studio policy** and **Studio currency, rates and hours** cards apply to the whole studio. Artists see preferences and a read-only policy.
- Studio logo: it now sits in its own box beside the SLATE wordmark, instead of over it.

### Light theme

- **Settings → Theme → Light.** Every screen takes its colours from the theme: header, sidebar, tables, dialogs and all the tabs. Light, Dark and the old "Slate" name (the same as Dark) all work.
- **Dark stays the default.** Nobody's look changes on upgrade.
- Menus, dialogs and new windows switch at once. Screens that are already open switch when you next start Slate.
- In every theme, video and image pictures are shown on black (a neutral surround for judging colour, as in RV or Nuke), and Home's background stays dark.
- **Shared look everywhere:** one height for all fields and buttons, chevron arrows on drop-downs, visible checkboxes, and one table style with a clear selection.
- In dialogs, **Enter presses the main button and never Cancel**.

## Fixed in this release

### Sign-in & window
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

### Home
- "Leave Management" and "IT Ticketing" tiles did nothing. Tiles appeared for screens you could not open.
- "My recent tasks" showed the first five shots in the database, for everybody.
- With the database down, Home stayed on "Loading…" forever. Without graphics acceleration it stuck on "INITIALIZING SINGULARITY".
- The animated background drew every frame forever, even behind the panels.
- Figures were cut in half on 768-pixel-high screens.

### VFX Dashboard
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

### Build & Ingest and CAP Rename
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

### Stock Viewer and Timeline Viewer
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
- **Launching the Timeline Viewer force-closed every Olive on the machine**, and could grab any window with "olive" in its title.
- Timeline shots were ordered by the last number only, mixing reels. DMP, CG, Roto, Matchmove and Slapcomp renders never appeared.

### Scheduling and Bidding
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

### Attendance and Leave
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

### Joining & Leaving, and Users & Roles
- **Typing a name in Start joining could start the checklist for a different person.**
- A person joining and leaving was merged into one row with 19 tasks. "Workstation returned" was ticked when only one of several machines came back. The joining date you typed was ignored.
- **Add New User with an existing username took over that account.**
- Supervisors could create Developer accounts. HR and IT could give themselves Full access.
- Removing Full access left every box ticked.
- Reports to, Employment, Joined and Location could never be cleared. Reports to accepted anyone, including loops.
- Import gave everybody the Admin role by default, and froze Slate for about 17 s per 100 people.

### IT Support, Hardware, Deployment and Licences
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

### Admin Panel, Live Ops, Data Center and Settings
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

### Light theme
- Choosing Light changed almost nothing: the old dark stylesheet was laid over every window. Every screen now follows the theme.

## Known limits

- **Opening a big project on the VFX Dashboard still loads on the main screen**, so Slate can pause for a moment. Filtering, the board and thumbnails no longer cause pauses, and 262 shots open fine.
- **Changing the theme** applies to menus, dialogs and new windows at once. Screens already open change when you restart Slate.
- The Scheduling **People view is read-only** in this version.
- CAP Rename reorders with **Move up / Move down** and sorting. Rows cannot be dragged.
- **Licence renewal reminders** are checked when someone in IT opens Licences or the IT queue (at most once an hour). They are not sent in the background.
- If a ticket's priority changes between P1 and P2–P4 while it has waiting time banked, that part keeps the clock it started with.
- The **Coordinator** role does not get "Force-save over others" by default. Tick it on the role if your studio wants it.
- Older shot history for a shot name that exists in two reels cannot always be tied to the right reel.
- **Check for updates** in Settings always shows a found update, even after "Remind me later". This is deliberate, since you asked.
- **When Slate works offline (local mode):**
  - Home's "today" can be off by a day for a short time around midnight;
  - tickets and deployments recorded offline before this version keep the time they were saved with, which may be UTC.
- Biometric files only pair a late IN with the next morning's OUT when the machine records whether each punch is an IN or an OUT.
