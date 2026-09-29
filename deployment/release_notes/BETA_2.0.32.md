# Slate BETA 2.0.32

Three installers, one per machine role. Install the server first, then the workstations.

| Installer | Install on | What it holds |
|---|---|---|
| `setup_Slate Server_vBETA 2.0.32.exe` | the one machine that runs the studio database | Slate Server, PostgreSQL 17, PgBouncer, the updater |
| `setup_Slate_Studio_vBETA 2.0.32.exe` | artist, lead and supervisor workstations | Slate Studio, ffmpeg, Olive, OpenRV, the updater |
| `setup_Slate_Ops_vBETA 2.0.32.exe` | HR, IT and production office machines | Slate Operations, ffmpeg, Olive, the updater |

Nothing needs an internet connection. Every dependency is inside the installer, and updates are picked up from the studio's shared folder.

## Upgrading from 2.0.31

Run the new installers over the old ones, server first. Settings and data are kept. Nothing in the database changes in this release.

## New in this release

**Open shots in NukeX**
- Nuke now opens as **NukeX** by default, from the shot panel's Open In buttons and from the dashboard's right-click menu. The buttons say "NukeX".
- To open plain Nuke or Nuke Studio instead: Settings → Core Configuration → **Open Nuke as**.
- Nuke and Blender open the shot's newest `.nk` / `.blend` if it has one, otherwise the program opens empty.

**Slate finds your programs by itself**
- Slate looks in Program Files for the **newest** installed Nuke, Blender, After Effects, Premiere, Silhouette and Natron. Nuke 16 is picked over Nuke 15, Blender 4.10 over 4.2.
- After you upgrade Nuke or Blender, Slate notices the old path has gone and finds the new one. You are only asked to locate a program when it cannot be found anywhere.
- A path set by hand, or through `Slate_NUKE_PATH` and the other environment variables, still comes first.

**README**
- New "Running a studio on it" section: first sign-in, opening shots, updating, backup and restore, network ports and firewall, a suggested server computer, troubleshooting, and where to get help.

## Fixed in this release

- **Opening a shot in Nuke, Blender, Silhouette or Natron did nothing.** Slate found the program and then stopped with an error before starting it.
- The **RV** button on the shot panel did nothing. It now opens RV review.
- When two people saved the same shot at once, the **Reload / Force save** window crashed instead of letting you choose.
- **Tester Panel → Smart Path Check** always failed. It now finds the rows that point at a folder, and folder names with `_` or `%` in them are matched exactly. **Ghost** mode works too.
- Two smaller crashes that could not be reached yet: the proxy clean-up on a file it cannot read, and the offline database sync, which now reports a failure instead of saying "Completed".

## Known limits

- The program search only knows the usual install folders. If a program lives somewhere else, locate it once when asked, or set it with the `Slate_<APP>_PATH` environment variable.
- The Silhouette and Natron folders are the usual ones from their installers, but have not been tried on a real install.
- Clicking **Save** in Settings writes back the settings that screen loaded when Slate started. A program path found after that can be forgotten; Slate simply searches again next time.
