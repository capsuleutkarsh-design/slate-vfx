# Slate BETA 2.0.27

Three installers, one per machine role. Install the server first, then the workstations.

| Installer | Install on | What it holds |
|---|---|---|
| `setup_Slate Server_vBETA 2.0.27.exe` | the one machine that runs the studio database | Slate Server, PostgreSQL 14, PgBouncer, the updater |
| `setup_Slate_Studio_vBETA 2.0.27.exe` | artist, lead and supervisor workstations | Slate Studio, ffmpeg, Olive, OpenRV, the updater |
| `setup_Slate_Ops_vBETA 2.0.27.exe` | HR, IT and production office machines | Slate Operations, ffmpeg, Olive, the updater |

Nothing needs an internet connection. Every dependency is inside the installer, and updates are picked up from the studio's shared folder.

## First start on a new studio

1. On the server machine, start Slate Server, choose the folder for the database, and click **Create a new empty database here** when asked.
2. On any workstation, sign in as **admin** / **admin123**, create the studio's accounts from the Users tab, and change the admin password. The `artist` and `tester` accounts are test accounts and can be deleted.
3. The tables are created by the first workstation that connects. Until then the server dashboard shows 0 tables, which is normal.

## Upgrading from an earlier build

Run the new installer over the old one. The programs now live under `AppData\Local\Programs`, away from the settings folder, so an upgrade no longer resets your settings, cache or offline database. Older program files left in `AppData\Local\Slate` are cleaned up by the installer; the settings there are kept.

Existing databases are converted automatically on the first connection from this build. No script has to be run.

## Fixed in this release

**Build and installers**
- The server is built once, from its spec, with its settings inside. A second build used to overwrite it with one that had no database password.
- ffmpeg is no longer packed twice, and the client installers no longer carry a copy of PostgreSQL. Installers are 50 to 110 MB smaller.
- The one-file server no longer unpacks pgAdmin and the PostgreSQL documentation on every start.
- All three programs install under `AppData\Local\Programs`.

**Updates**
- Update packages carry a real version number. A package labelled "latest" was offered on every launch, forever.
- The server installer includes the updater, so a server can apply an update rather than only download it.
- A failed update's rollback keeps the database, logs and settings instead of deleting them.

**Server**
- The window opens at a usable size and remembers it; the dashboard fits any width down to the minimum.
- Pop-up dialogs are drawn in the dark theme and their buttons show in full.
- The web dashboard starts from an installed server, not only from a developer checkout.

**Data**
- Leave requests are now actually saved on PostgreSQL. Previously the save was refused by the database and reported as successful, so no request was ever stored on a real server.
- Other leave actions report a failed save instead of a false success.
- A new studio database tells you to sign in as `admin` instead of "invalid credentials".

## Known limits

- PostgreSQL stays on version 14. Every existing data directory is 14; moving to 16 needs `pg_upgrade`, not a newer binary.
- Studio and Operations share the settings folder `AppData\Local\Slate`. Uninstalling Studio with "delete data" keeps the server path if Operations is still installed.
