# Slate BETA 2.0.29

Three installers, one per machine role. Install the server first, then the workstations.

| Installer | Install on | What it holds |
|---|---|---|
| `setup_Slate Server_vBETA 2.0.29.exe` | the one machine that runs the studio database | Slate Server, PostgreSQL 17, PgBouncer, the updater |
| `setup_Slate_Studio_vBETA 2.0.29.exe` | artist, lead and supervisor workstations | Slate Studio, ffmpeg, Olive, OpenRV, the updater |
| `setup_Slate_Ops_vBETA 2.0.29.exe` | HR, IT and production office machines | Slate Operations, ffmpeg, Olive, the updater |

Nothing needs an internet connection. Every dependency is inside the installer, and updates are picked up from the studio's shared folder.

## Upgrading from 2.0.27 or 2.0.28

Run the new installers over the old ones. Settings are kept.

The database engine moved from PostgreSQL 14 to 17, and a 14 data folder cannot be opened by 17. On a server that is still in testing: stop the server, move `AppData\Local\Slate_Central\LocalDatabase` aside, start the new server and choose "Create a new empty database here", then sign in as admin from one workstation. On a server with real data: take a backup with the old server first, install the new one, create a new empty database, then Operations → Restore with that backup.

The server is now a folder rather than a single file. The installer removes the old `Slate_Server.exe` and puts the folder in its place; the database is untouched because it never lived in the program folder.

## Fixed in this release

**Server**
- PostgreSQL 17.11 replaces 14.15. Version 14 leaves community support in November 2026; 17 is supported to 2029 and adds incremental backups and the `pg_stat_io` view. A data folder written by 14 is refused with a message saying so, instead of a failed start with the reason buried in `pg_server.log`. Studios still testing should create a new empty database; a studio with real data moves it across once with `pg_upgrade` or restores a backup taken with the old server.
- The server installs as a folder, like the clients. It used to be a single file that unpacked PostgreSQL and everything else into `%TEMP%` on every start and ran from there, so cleaning the temp folder while the server ran broke the running server. Nothing Slate needs lives in temp any more, and the server starts faster.
- pgAdmin and the PostgreSQL documentation are no longer shipped with the server.

**Ports**
- If the database port is already held by another program, the server says which program and stops, instead of failing inside PostgreSQL with the reason buried in `pg_server.log`.
- The server's announcement on the network now carries both its ports. A workstation that knows the server but was set up for an old port follows the change on its own; the announcement used to be ignored whenever the address was already known. Workstations that reach the server through the pool never cared about the database port.

**Attendance and leave (Operations)**
- The Attendance tab opens again. It died on open with "name 'can' is not defined" for every Operations user.
- HR can import attendance from the studio's biometric machine: Attendance → IMPORT BIOMETRIC, choose the CSV or Excel export. Slate works out which column is the employee code, date, time and direction, lets you correct it, remembers the layout for next time, and asks which person an unknown code belongs to. Importing the same file twice changes nothing.

**IT Support (Operations)**
- Replies under a ticket in My Tickets are now saved on a fresh install. The table they go to was created only by a development-machine migration, so on every studio the thread silently stayed empty.

**Starting up**
- A workstation whose server firewall drops the pool port no longer waits twenty seconds before the login screen. A pool that did not answer is left alone for two minutes.
- The server's "Allow firewall" button opens the pool port as well as the database port.
- The workstation backup thread no longer logs "backup script not found" every twelve hours. Backups are the server's job; the thread says so once and stops.

**Stock Browser and playback**
- The program no longer dies without a word when the studio folder drops out while a background job (update check, backup, ingest) is running. The "studio folder not found" notice was being opened from that background thread, which Qt cannot survive; it is now shown from the interface, without blocking.
- The ingest hands the gallery its own copies of each asset instead of sharing objects between the scanning thread and the screen.
- Thumbnails and proxies are written under a working name and renamed when complete. A clip clicked while its proxy is still encoding plays the source, not a half-written file, and a proxy cut short by a crash is redone rather than trusted.
- A crash inside a native library now leaves a stack for every thread in `AppData\Local\Slate\Logs\native_crash.log`. Send that file with the bug report.
- The image engine reads pictures through its bundled plugins in the installed build instead of falling back to a slower path.

**Large libraries**
- The ingest writes its own memory use to the log every 250 assets - working set, threads, child processes, handles and pictures held - so a run that grows out of hand can be traced to where it started and what it is made of.
- If the program's memory passes 40% of the machine (never less than 6 GB), the ingest pauses itself and says so, instead of taking the workstation down. Save, restart Slate, run the ingest again; it continues where it stopped.
- While an asset is still being ingested, the gallery no longer decodes its full-size source picture off the share for a placeholder; it waits for the thumbnail the ingest is about to make.

## Known limits

- The cause of a day-long ingest reaching 100 GB has not been found; it could not be reproduced on a test machine. The memory log above and the pause are there so the next such run can be read. Send `AppData\Local\Slate\Logs\latest.log` from the machine it happened on.
- Studio and Operations share the settings folder `AppData\Local\Slate`. Uninstalling Studio with "delete data" keeps the server path if Operations is still installed.
