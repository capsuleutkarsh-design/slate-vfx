# Slate BETA 2.2.0 – security

This release closes the security holes found in the review of 5 October 2026. It is built so that it **cannot lock you out**:

- **A fix that cannot stop anybody signing in is simply made.**
- **A fix that could stop somebody is behind a switch, and every switch starts OFF.** You turn switches on yourself, one at a time, from **Recover Slate** on the server PC with the Recovery Key. Turning one on first checks that people can still get in. It takes a snapshot so it can be undone, and it is rolled back by itself if the check afterwards fails.
- **The Recovery Key is the way back in when nothing else works.** Read `docs/RECOVERY.md` and keep a printed copy with the key.

Install the server first, then the workstations, as before.

## Before you install

1. Back up the studio database (Slate Server → Operations → Back up now).
2. Install **Slate Server 2.2.0** on the server PC. The first start shows the **Recovery Key once**. Write it down and keep it with a printed `RECOVERY.md`.
3. Install Studio or Ops 2.2.0 on one admin workstation, sign in, and check Users & Roles. Then install the rest.

## Fixed in this release (no switch needed)

**The server**
- **Slate Server no longer starts the web API.** Anyone on the network could use it to make themselves an administrator. Nothing in Slate used it. The "Open web dashboard" button and the "Web API port" setting are gone. The API's sign-in now checks the password, and its routes that could create users or overwrite shots are deleted.
- **"Disconnect session" disconnects the session you selected.** The list showed one set of connections while the button acted on another, so it could end the wrong person's session. The list also refreshes without freezing the window.
- **Settings say "NOT saved" when they were not saved.** This includes the database password. A damaged `slate_server_config.json` is no longer overwritten; you are told to fix it or move it aside.

**Accounts and sign-in**
- **A `users.json` or `roles.json` on the share is read only once, on an empty database.** Before, anyone who could write to the share could make themselves a Developer, or reset admin's password, at the next start of any PC.
- **The shared `admin123` master password no longer approves fleet-wide wipe, restart or shut down.** Admins confirm with their own password, and the setting is removed from the default configuration.
- **The test accounts artist/artist123 and tester/tester123 are no longer created** on a new database.
- **Old password formats are upgraded at sign-in.** A password stored as plain text, or in the old unsalted format, is re-stored securely the next time that person signs in.
- **Every sign-in is recorded** in the audit log: success, and each failure with the reason, but never the password.
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

## Switches you can turn on (all OFF after installing)

Open **Recover Slate** on the server PC, unlock it with the Recovery Key, and use **Security switches**. Each switch has **Log only**, which logs what it would refuse and refuses nothing, and **Turn on**. Try **Log only** for a day first and read the log. Any switch can be turned off again from the same page, even when the database is down.

| Switch | What it does when on | Before turning it on |
|---|---|---|
| `split_superuser_password` | The database superuser gets its own password, kept on the server PC only. Workstations keep theirs. | Press **Restart pool** afterwards. |
| `strict_pg_hba` | The superuser can connect only from the server PC. Workstations can reach only the studio database. | Refused while a workstation is connected as the superuser. |
| `refuse_inactive_signin` | People who are deactivated, or past their last working day, cannot sign in. `admin` and `developer` are always exempt. | Check Users & Roles for anyone wrongly marked as left. |
| `no_plaintext_passwords` | Passwords in the old formats no longer open an account. | Refused while any active account still has one: let everybody sign in once first. |
| `no_default_accounts` | admin/admin123 is never created or brought back. The first-run screen points to Recover Slate instead. | Make sure you have the Recovery Key. |
| `no_sqlite_fallback` | When the server is down, workstations do not open the local copy, which has its own admin/admin123. | With this on, nobody can work while the server is down. |

`signed_fleet_commands`, `signed_updates` and `pgbouncer_hba` are listed but not built yet; turning them on is refused.

## Still open (planned)

- **The database password `Tango$`** is still the shipped default. It is public. Changing it safely is planned for **2.3**:
  1. workstations read the password from Windows' protected store;
  2. the new password is rolled out to every workstation;
  3. it is changed on the server;
  4. it is removed from the repository's history.

  Doing it in any other order disconnects every PC.
- **Fleet commands and updates on the share are not yet signed**, so anyone who can write to that share folder can post them.
- **Permissions are checked in the app, not by the database.** Anyone with the database password can bypass them. This changes after the password is protected.
- **The audit trail is still a file on the share.** It moves to the database together with that change.
- **A crafted SQL console query can still show a password hash** (for example `row_to_json`). This needs a database account that cannot read that column.
