# How Slate is put together

Written against the code as it stands. If something here disagrees with the
source, the source is right and this page is a bug — say so.

---

## The shape of it

Slate is a **PySide6 desktop application** talking to a **PostgreSQL** database,
with a local SQLite database as a fallback so a workstation that cannot reach
the server still starts and still works.

There are four entry points:

| | |
|---|---|
| `ut_vfx/vfx_studio_main.py` | the main client — `Slate.bat` |
| `ut_vfx/studio_ops_main.py` | the operations shell — `Slate Ops.bat` |
| `ut_vfx/gatekeeper_main.py` | the login screen, which launches one of the above |
| `ut_server/main.py` | Central Server — `Slate Server.bat` |

---

## The layers

```
ut_vfx/
  gui/          Qt. Windows, tabs, widgets, the design system.
  core/
    domain/     The rules. What the studio does.
    infra/      The machinery. Database, config, logging, networking.
  utils/        Media and filesystem helpers that predate the split.
```

The line that matters is between `domain` and everything else.

**`core/domain` holds policy and nothing else.** No Qt imports, no SQL. These
are the files to read to learn what the studio actually does:

| | |
|---|---|
| `leave_policy.py` | the working week, accrual, the sandwich rule, the approval chain |
| `service_desk.py` | ticket categories, the impact × urgency matrix, SLA hours |
| `licence_compliance.py` | what over-subscribed, under-used and renews-soon mean |
| `onboarding_service.py` | the joining checklist and its mirror image |
| `workplace_access.py` | which side of Leave and Tickets a person gets |
| `comp_off_service.py` | turning attendance into comp-off |

Because they have no dependencies, they are directly testable — `tests/test_workplace_domain.py`
exercises all of the above with no database and no Qt.

**`core/infra` holds everything with a dependency on the outside world.** The
repositories (`leave_repository`, `licence_repository`, `stock_repository`,
`project_repository`, …) are the only places that write SQL. A screen that
builds its own query is a bug — that is how the balance on one screen and the
queue on another end up disagreeing about the same person.

---

## The database

Selected at startup by `core/infra/database_manager.py`, which is a proxy in
front of one of two backends:

```
db_mode: "postgres"  ->  PostgresManager   the studio database
db_mode: "sqlite"    ->  SQLiteManager     standalone, zero-config
```

**Falling back is deliberate.** If `postgres` is requested and the server cannot
be reached, `DatabaseManager` drops to SQLite rather than refusing to start, and
records that it did. An artist on a laptop with no VPN gets a working
application instead of a crash dialog. Set `allow_db_fallback: false` to make
that a hard failure instead.

`SQLiteManager` translates on the way through so the repositories can be written
once, against PostgreSQL: `%s` to `?`, `ILIKE` to `LIKE`, `TRUNCATE` to
`DELETE`, `::jsonb` casts stripped, `RETURNING id` intercepted via
`cursor.lastrowid`. Rows come back as dicts from both backends.

Two consequences worth knowing when writing a query:

- **`is_completed` is a real boolean on PostgreSQL and an integer on SQLite.** There is no literal both accept in every position, so filter in Python where it matters.
- **Some older columns are TEXT that hold dates** (`attendance_log.day_date`). Comparisons against them are string comparisons — ISO strings sort correctly, which is the only reason it works, and it cannot use a date index.

### When the database is unwell

`core/infra/circuit_breaker.py` wraps the PostgreSQL calls. After **5 consecutive
failures** it opens for **60 seconds** and fails fast instead of making every
screen wait on a connection that is not coming.

A failed query **raises** `DatabaseUnavailableError`. It does not return `None`,
and a failed save does not return `False` — a connection shortage used to look
exactly like an empty project and a save that worked, which is the failure a
busy studio actually hits.

### Migrations

`core/infra/migrations/` — run at startup, additive, and safe to run twice:

| | |
|---|---|
| `auto_migrate.py` | the phase-1 set, PostgreSQL only |
| `shot_identity.py` | widens the shot key; runs on both backends every start |
| `stock_indexes.py` | the stock library's indexes |
| `workplace_schema.py` | leave, comp-off, holidays, asset assignments, licence readings |
| `delivery_batches.py` | delivery grouping |

`workplace_schema.py` only ever adds tables and columns. Nothing in it drops or
rewrites anything, so running an older client against a newer database is
awkward rather than destructive.

> **`execute_update` returns a row count.** DDL affects zero rows, so its return
> value says nothing about whether a `CREATE TABLE` worked. A migration that
> reports "0 tables created" may well have created four.

---

## Configuration

Layered, lowest priority first. Each layer overrides the one above it:

1. `GlobalConfig.DEFAULTS` — hard-coded
2. `ut_vfx/default_config.json` — shipped with the source. **Settings only, no credentials.**
3. `ut_vfx/config.json` — written by `setup.bat`, git-ignored, **this is where the password lives**
4. `client_config.json` — per-site overrides
5. `%LOCALAPPDATA%\UTVFX\config.json` — per-machine

`core/infra/local_secrets.py` is the one place that resolves a credential. It
checks the `SLATE_DB_PASSWORD` environment variable first, then the local
configs in order. Maintenance scripts import it rather than carrying a literal —
this repository is public, and a password in source is a disclosure.

---

## Threads

Qt's event loop is single-threaded, and a blocked event loop is a frozen window.
Anything that touches a disk, a network or FFmpeg runs off the main thread.

There are roughly thirty worker classes; the pattern is consistent:

- **`QThread` subclasses** for long jobs that report progress — `IngestWorker`, `MoveScanWorker`, `ProxyBuildWorker`, `ExcelLoadWorker`.
- **`QRunnable` on a pool** for many small jobs — `ImageLoaderTask` for thumbnails.
- **`GlobalTaskRegistry`** (`core/infra/task_registry.py`) keeps track of what is running so shutdown can stop it.

Two rules learned the hard way:

- **Only the main thread touches widgets.** Workers emit signals; slots on the main thread update the UI.
- **A task must survive interpreter shutdown.** `ImageLoaderTask.run()` is wrapped because a thumbnail finishing as Python tears down was a segfault at the end of the test suite.

---

## The interface

One palette, in `core/infra/gate.py`, and one design system in `gui/core/`.

| | |
|---|---|
| `gate.py` | every colour, spacing step, radius and font in the product |
| `gui/core/icons.py` | 31 drawn SVG icons, tinted at draw time |
| `gui/core/icons_brand.py` | the Slate mark — the header, login, window icon and installer art all come from this one path |
| `gui/core/controls.py` | `make_button`, `page_title`, selection gating |
| `gui/core/empty_state.py` | what a table says when it has nothing in it |
| `resources/styles/main.qss` | the global stylesheet, with `@TOKEN` names resolved from `gate.py` at load |

**Chrome is achromatic. Saturation is reserved for meaning.** A red row means
the studio is out of compliance, not that red looked urgent.

> **A widget's own stylesheet beats the application's.** This is the single most
> important thing to know before styling anything here. The product once had 698
> inline `setStyleSheet` calls overruling a 10,000-character global sheet that
> changed 0.02% of pixels. If you hard-code a hex in a widget, you have started
> that again.

Two Qt details that cost real time:

- **A bare `QWidget` subclass ignores stylesheet backgrounds and borders** unless you set `Qt.WidgetAttribute.WA_StyledBackground`.
- **Mixin order matters.** With `QMainWindow` listed before a mixin, the mixin's `resizeEvent` never runs, because the Qt base class shadows it.

---

## The sidebar

Registered in `gui/components/main_window_builder.py`, grouped into four
sections:

```
PRODUCTION      Home · Build & Ingest · CAP Rename · Stock Viewer
                Timeline Viewer · VFX Dashboard · Scheduling · Bidding
HRMS            Home · Attendance · Leave · Joining & Leaving
IT & INFRA      Hardware · Licences · IT Support · Deployment
ADMINISTRATION  Users & Roles · Admin Panel
SYSTEM          Tester Panel · Settings
```

Tabs are **lazy** — registered as factories and built the first time they are
opened, so startup does not pay for eighteen screens.

Three entries build a different widget depending on who is looking:

```python
Leave            -> LeaveApprovalsView(stage="HR")          HR
                    LeaveApprovalsView(stage="Supervisor")  supervisor, lead
                    MyLeaveView                             everyone else

IT Support       -> ServiceDeskView                         IT
                    MyTicketsView                           everyone else

Joining & Leaving-> JoiningLeavingView(team="IT")           IT
                    JoiningLeavingView(team="HR")           HR
                    not registered at all                   everyone else
```

`core/domain/workplace_access.py` makes that decision. Note the third case: a
tab nobody can act on is **absent, not disabled**. A locked entry is an
invitation to ask why.

---

## Further reading

- [Installing it](install.md)
- [Working on it](development.md)
- [The studio guide](guide/) — what each role actually sees
