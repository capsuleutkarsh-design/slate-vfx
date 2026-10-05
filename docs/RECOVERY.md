# If nobody can sign in to Slate

This page is for the studio's Slate administrator. Keep a printed copy with
the Recovery Key.

You need two things:

1. **The server PC.** This is the PC that runs Slate Server and holds the studio's database.
2. **The Recovery Key.** Slate showed it once, when the server was set up or the first time
   this version started on it. It looks like `XXXXX-XXXXX-XXXXX-XXXXX-XXXXX`.

You do **not** need any Slate password or database password.

---

## 1. Open Recover Slate on the server PC

Use whichever of these works:

- **Installed server:** Start menu → **Recover Slate**.
- **Slate Server window still opens:** click **Recovery** in its left-hand menu.
- **Copy of the Slate folder (checkout):** double-click **`Recover Slate.bat`**.

The window opens with a **Health check**. It lists what is wrong in plain words, with what to
do next to each item. The health check does not change anything, and it does not need the key.

Recover Slate will not run on any other PC. It refuses if the database folder is not on this
PC's own disk.

## 2. Unlock

Type the Recovery Key into **Unlock** and press **Unlock**. Capital letters, spaces and dashes
do not matter. The page stays unlocked for ten minutes.

If you type the wrong key three times, Slate makes you wait before the next try. The first wait
is 5 seconds, and each wait after that is longer, up to 5 minutes. A wrong key never locks you
out for good.

## 3. Fix what is wrong

| What happened | What to press |
|---|---|
| Somebody forgot their password, or was switched off by mistake | **Accounts**: type the username and a new password twice, then press **Reset password and switch the account back on**. They must choose their own password the next time they sign in. |
| No administrator is left (deleted, switched off or demoted) | **Accounts**: type a username (for example `admin`) and a new password, then press **Create or restore administrator**. |
| Workstations say the database password is wrong | **Database passwords**: press **Show app password**. It shows the password in use and the one before the last change. Either give the one in use to those workstations (Reconfigure, below), or type the one they **already have** and press **Set app password (workstations)**. |
| The database folder was moved to a new server PC | The passwords the old PC kept cannot be read on the new one (the health check says so). **Database passwords**: type the password the workstations have (keep it with the Recovery Key), then press **Set app password (workstations)**. |
| A new workstation needs the database password | **Database passwords** > **Show app password**, then on that PC: **Reconfigure server / database** on the sign-in screen. |
| The server says it cannot log in to its own database | **Database passwords**: type a new password, then press **Set superuser password (this server)**. Workstations are not affected. Keep this password with the Recovery Key. |
| People stopped being able to work after a security setting was turned on | **Security switches**: choose the switch, or **Every security switch**, then press **Turn off**. This works even when the database is down. |
| A settings file or the access rules got damaged | **Restore the last snapshot**. Tick **Also put the users and roles back** only if accounts were damaged too. |
| "An interrupted recovery left the access rules open" | Press **Close an interrupted repair**. Starting Slate Server also closes it. |
| "A connection pool is still running from an earlier start" | Press **Stop leftover pool**, or just start Slate Server. |

Then press **Check now** again. When everything is OK, people can sign in.

### Turning a security switch on

Unlock, then in **Security switches** choose one switch. The line under it says what the switch
does and whether it is off, log only or on.

- **Log only** changes nothing. Slate Server writes in its log what the switch would refuse.
  Use it first, always for the `signed_...` switches.
- **Turn on** checks first that people can still get in. If they could not, it refuses, says
  why, and changes nothing. If the check after the change fails, the change is undone at once.
- **Turn off** puts the old behaviour back. For `split_superuser_password` the superuser gets
  the workstations' password again. For `strict_pg_hba` the previous access rules come back.

### Changing the workstations' password (app password)

Every studio has its own database password. Slate Server makes it, and keeps it on the server
PC only (in `slate_recovery\db_secrets.dat`, encrypted by Windows). Workstations keep it in
Windows Credential Manager, never in a file.

To change it without cutting anybody off:

1. **Publish a new app password.** Nothing changes yet. Each workstation on Slate 2.2.0 or
   later learns it the next time it connects.
2. **Show app password** lists the workstations that have it. Wait until every one is there
   (each has to open Slate once).
3. **Switch to the published password.** It is checked first, and undone on its own if the
   server cannot get in afterwards. The workstations that learned it follow on their own.
4. Any workstation that did not learn it: click **Reconfigure server / database** on its
   sign-in screen and type the password **Show app password** gives.

**Set app password (workstations)** changes it at once instead: every workstation without the
new password is cut off until it is typed in there. If you only want people working again, set
it back to the one the workstations already have (**Show app password** lists the previous
one).

### Upgrading a studio that used the old shipped password

Slate before 2.2.0 shipped one database password with every copy, and it is public. On its
first start, Slate Server 2.2.0 keeps using it (nobody is cut off) and publishes the studio's
own new password (step 1 above). The health check warns until you switch. So: update the
server, update every workstation and open Slate on each, then steps 2 to 4.

### The server PC's address changed

The health check will say so. On each workstation, click **Reconfigure server / database** on the
sign-in screen and enter the new address. To stop this happening again, ask IT to give the
server a fixed address (a DHCP reservation).

### The clock is wrong

The health check warns when the PC's clock is behind. Recovery still works. Fix the date and
time in Windows Settings → Time, because Slate uses this clock to decide when someone's last day
has passed.

---

## If the Recovery Key is lost

Make a new key. You need Windows administrator rights on the server PC:

1. Right-click **Recover Slate** (or `Recover Slate.bat`) and choose **Run as administrator**.
2. Leave the key box empty and press **Make a new Recovery Key**.
3. Print the new key, or write it down. The old key stops working straight away.

If you still have the old key, type it into **Unlock** first, then press **Make a new Recovery
Key**. You do not need administrator rights for this.

If nobody has Windows administrator rights on the server PC, ask your IT person to do this.

## What happens behind the scenes

These details are here for IT staff.

- **How the tool gets in.** For a few seconds, the tool replaces `pg_hba.conf` with one rule. That
  rule lets only the `postgres` superuser in, and only from this PC (`127.0.0.1` and `::1`), with
  no password. The original file is written to `slate_recovery\trust_window.json` before it is
  replaced. The original file is put back three ways:
  - right after the change, in a `finally`;
  - by a timer after at most two minutes;
  - if the PC crashes, when Slate Server or the tool next starts.

  If the database was not running, the tool starts it listening on `127.0.0.1` only, then stops
  it again.
- **Snapshots.** Before every repair and every security change, Slate copies these files into
  `slate_recovery\snapshots\NNNN_<time>_<what>\`, with a `manifest.json`:
  - the access rules;
  - `postgresql.conf`;
  - the server's settings;
  - the password settings file and `db_secrets.dat` (the passwords, encrypted);
  - the PgBouncer files;
  - the accounts (`pg_dump` plus JSON).

  The first snapshot and the newest hundred are kept.
- **The `slate_recovery` folder.** It sits next to the database folder. Only the administrators
  and the account that runs Slate Server can read it. It holds:
  - the Recovery Key's scrypt fingerprint (never the key itself);
  - the wrong-key count;
  - the snapshots;
  - `recovery.log`, which records what was done, when and by whom, but never a password or the
    key.
- **Command line.** `Recover Slate.bat --help` lists every command, for example
  `Recover Slate.bat health` and `Recover Slate.bat reset-password admin`. On an installed
  server, use `Slate_Server.exe --recover <command>`.
