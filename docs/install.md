# Installing Slate

**Clone or download the repository, then double-click `setup.bat`.** That is the
whole install on a workstation.

Everything below is what it does and what to do when it does not.

---

## Requirements

- Windows 10 or 11, 64-bit
- About **300 MB** of downloads on a workstation, **600 MB** on the server machine
- No Python needed beforehand — Slate brings its own

Nothing goes into the registry or onto `PATH`. No Python already on the machine
is touched. Deleting the folder removes Slate completely.

---

## Running it

```
setup.bat              install what is missing
setup.bat /check       say what it would do, download nothing
setup.bat /force       re-download even what is already there
setup.bat /server      also install PostgreSQL (the Central Server machine only)
setup.bat /skipconfig  leave slate/config.json alone
```

Start with `setup.bat /check` on a machine you are unsure about. It contacts
every download and reports the size without fetching anything.

**Running it twice is safe.** Each component is probed before it is fetched, so
a second run skips what is already there, and an interrupted download resumes
rather than leaving a half file the next run mistakes for a whole one.

---

## What it installs

Pinned in `setup/components.json`. Edit that file to change a version; the
script reads it rather than hard-coding anything.

| | | |
|---|---|---|
| **Python 3.10** | required | `runtime/python` — portable, not system-wide |
| **pip** | required | bootstrapped into that runtime |
| **FFmpeg** | required | `slate/bin/ffmpeg.exe` — thumbnails, proxies, playback |
| **PostgreSQL 16** | server only | `slate_server/bin/pgsql` — skipped without `/server` |
| **Olive** | optional | `external/olive-editor` — the review tab falls back without it |
| **OpenRV** | manual | `OpenRV/` — no public Windows build; drop yours in and Slate finds it |

None of it is committed. These are third-party builds with their own licences
and their own release cadence, and together they are the better part of a
gigabyte — pinning them keeps the repository at 8 MB.

Olive's nightly asset carries the build's commit hash in its filename, so that
one is resolved through the GitHub API at install time rather than pinned to a
link that stops working within the week.

---

## The questions it asks

Once, at the end:

```
Database host  [localhost]
Port           [5440]
Database name  [slate]
Database user  [ut_vfx_app]
Database password
Admin password (for Slate's own admin panel)
```

These are written to **`slate/config.json`**, which is git-ignored and never
leaves the machine. Delete that file and re-run `setup.bat` to change them.

### Without a server

**Leave the database password blank.** Slate starts on a local SQLite database
instead. Everything works; nothing is shared between machines. Re-run
`setup.bat` when the server is ready.

---

## Afterwards

Setup writes three launchers:

| | |
|---|---|
| `Slate.bat` | the main client |
| `Slate Ops.bat` | the operations shell |
| `Slate Server.bat` | Central Server — PostgreSQL, the PgBouncer pooler, sync |

---

## Setting up the server

On the one machine that hosts the database:

```
setup.bat /server
```

Then `Slate Server.bat`. The server writes its own `pgbouncer.ini` and
`userlist.txt` at startup — both are git-ignored and both are overwritten every
time, so do not edit them by hand.

Workstations do **not** need PostgreSQL. They connect over the network, and fall
back to a local SQLite copy if they cannot.

### Locking the database down

`deployment/secure_database.sql` closes the "any address, no password" rule that
PostgreSQL ships with. Passwords are supplied when you run it, not written into
it:

```
psql -v studio_password="'the password'" -f deployment/secure_database.sql
```

`tests/test_database_hardening.py` checks that this stayed done. On a machine
with no cluster those checks skip; wherever a cluster exists they run, and they
fail loudly if the door is reopened.

---

## When it goes wrong

**"The download link is not reachable"** — the pinned URL moved. Run
`setup.bat /check` to see which one, and correct it in `setup/components.json`.
A required component stops the install; an optional one is reported and stepped
over.

**"PySide6 will not import"** — the dependency install did not finish. Re-run
`setup.bat`; it resumes. If it repeats, run the pip step by hand to see the real
error:

```
runtime\python\python.exe -m pip install -r requirements.txt
```

**Slate starts but says it is in fallback mode** — it could not reach the
database and is on local SQLite. Check the host and port in `slate/config.json`,
that `Slate Server.bat` is running on the server, and that TCP 5440 is open.

**"Circuit breaker is OPEN"** — five database calls failed in a row, so Slate
stopped trying for 60 seconds rather than making every screen wait. It recovers
on its own. If it keeps happening the database is genuinely unwell, not Slate.

**Credentials for a one-off maintenance session** — set the environment
variable instead of writing a file:

```
set SLATE_DB_PASSWORD=...
```

It wins over every config file.

---

## Verifying an install

```
runtime\python\python.exe -m pytest tests/ -q
```

1,193 tests. Some skip where this machine has no PostgreSQL cluster and no
credentials configured — that is expected on a fresh workstation and the skip
messages say so.
