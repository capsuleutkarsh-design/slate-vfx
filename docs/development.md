# Working on Slate

Read [architecture.md](architecture.md) first — it explains where things live.
This page is about how to change them without breaking the conventions that
hold the codebase together.

---

## Getting set up

```
setup.bat
runtime\python\python.exe -m pytest tests/ -q
```

That is the development environment too. There is no separate one.

```
runtime\python\python.exe -m pytest tests/ -q                  everything
runtime\python\python.exe -m pytest tests/test_workplace_domain.py -q
runtime\python\python.exe -m pytest tests/ -q -k leave         by name
runtime\python\python.exe -m pytest tests/ -q --no-cov         faster
```

**1,193 tests.** They run in about a minute. Run them before you push.

Some skip on a machine with no PostgreSQL cluster and no configured
credentials — the skip message says which and why. That is correct on a fresh
workstation; it is not a broken test.

---

## The four conventions

### 1. Policy lives in `core/domain`, once

Leave types, ticket priorities, SLA hours, licence thresholds — each defined in
exactly one module, with no Qt import and no SQL.

If a screen works out a day count itself, that is a bug waiting to happen: the
balance on one screen and the queue on another will disagree about the same
person, and nobody will be able to say which is right.

This has already gone wrong here once. `leave_policy.py` was written as the
single source, and a view was left importing an entitlement constant from
`workplace_access.py` — two definitions of the same rule, three days after
removing the last pair.

### 2. Colour comes from `gate.py`

```python
from slate.core.infra.gate import Gate
label.setStyleSheet(f"color: {Gate.TEXT_DIM};")
```

Never a literal hex. Chrome is achromatic; saturation carries meaning —
`Gate.BAD` is for "this is wrong", not for "this should stand out".

**A widget's own stylesheet beats the application's.** The product once carried
698 inline `setStyleSheet` calls that between them overruled the entire global
theme; the measured effect of the global sheet was 0.02% of pixels. Reach for
`gui/core/controls.py` before writing a stylesheet at all.

### 3. SQL lives in a repository

`leave_repository`, `licence_repository`, `stock_repository`,
`project_repository`, and so on in `core/infra`. A view calls a repository
method; it does not build a query.

### 4. A screen nobody can act on is absent, not disabled

If a role has no business on a tab, do not register it. A greyed-out entry is an
invitation to ask why, and a permission toast is a worse answer than an empty
sidebar.

---

## Adding a screen

1. **Write the rule first**, in `core/domain`, with a test. No Qt, no SQL.
2. **Add the reads and writes** to a repository in `core/infra`. Return dicts.
3. **Build the view** in `gui/tabs/`, using `gate.py` for colour and `gui/core/controls.py` for buttons and titles.
4. **Register it** in `gui/components/main_window_builder.py` as a factory, so it stays lazy.
5. **Give it an empty state.** `gui/core/empty_state.py` — a blank table is the thing that makes a working module look broken.

If the screen differs by role, do what Leave does: one registration, a
`_build_*_tab` method that returns a different widget, and the decision made in
`core/domain/workplace_access.py`.

---

## Changing the schema

Add to `core/infra/migrations/workplace_schema.py`. It runs at startup on both
backends and is **additive only** — nothing in it drops or rewrites a column, so
an older client against a newer database is awkward rather than destructive.

Two traps:

- **`execute_update` returns a row count.** DDL affects zero rows, so a successful `CREATE TABLE` returns 0. Do not treat its return value as success.
- **Booleans differ.** `is_completed` is a real boolean on PostgreSQL and an integer on SQLite. Pass Python `True`/`False` as a parameter — both drivers handle that — and avoid a SQL literal that only one accepts.

---

## Testing

Tests live in `tests/`, and the ones worth copying as a pattern are
`tests/test_workplace_domain.py` — pure policy, no database, no Qt, and every
test named for the behaviour rather than the function.

Some of those exist because the bug shipped:

- `test_title_case_does_not_lose_a_pending_request` — `"Pending HR".title()` gives `"Pending Hr"`, which matched no constant, so requests waiting on HR silently stopped counting as pending and vanished from the artist's balance.
- `test_a_month_completes_on_the_joining_day_not_the_month_end` — accrual counted calendar months, so somebody joining on the 15th was credited a full month's leave on the 31st.

**When you fix a bug, write the test that would have caught it**, and say in the
docstring what actually went wrong. A test named after a defect is worth three
named after a method.

### GUI tests

They build real windows. They need a display and they need the database
reachable, or they error rather than fail. If `tests/gui/` errors on your
machine, check `Slate Server.bat` is running before looking at the code.

---

## Credentials

**This repository is public.** No password goes in source, ever — not in a
config, not in a maintenance script, not in a test fixture.

```python
from slate.core.infra.local_secrets import db_password
password = db_password()
```

That checks `SLATE_DB_PASSWORD` first, then the git-ignored local configs. It
raises with an explanation when there is nothing configured, rather than
connecting as nobody and failing later.

`slate/default_config.json` is committed and carries **settings only**. If you
find yourself adding a secret to it, the answer is `slate/config.json`, which
`.gitignore` already covers.

---

## Before you push

```
runtime\python\python.exe -m pytest tests/ -q
git diff --cached | grep -inE "password|secret|api[_-]?key"
```

The second one takes two seconds and has already caught a password in six source
files, a `.secret.key` sitting next to the file it decrypts, and a live
backup-encryption key left behind by a test run.
