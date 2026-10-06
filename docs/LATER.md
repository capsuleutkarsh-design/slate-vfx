# Slate – left for later

Agreed with the owner on 2026-10-06, before switching focus to Contour. **When work on Slate starts again, go through this list with the owner first.**

## 1. Connection sweep (the owner asked to come back to this)
The audits checked Slate one area at a time and missed problems *between* parts. The handbook pass found 23 of them. Sweep every contract two parts must agree on:
- folder names;
- status words;
- setting keys;
- permissions;
- database tables (who writes, who reads);
- notifications and signals.

Add end-to-end tests of whole journeys on a real database:
- a new studio → the first admin → an artist punches in;
- a leave request from the supervisor to HR and back to the person;
- a shot from ingest to review to verdict to the dashboard.

Start from CodeMap: copy `Documents\CodeMap` into the repo, or drop the repo folder on `CodeMap.bat`. On 2026-10-06 it showed 134 hints (13 "likely") and 266 dead-code candidates.

## 2. Small issues noted for the sweep
- The full test run occasionally hangs early on (seen twice). It is now run with `-o faulthandler_timeout=300` so the next hang shows where.
- A `ThumbnailLoader` "already deleted" error prints when Python exits after the tests.
- The installer ships the repo's `database/` folder of old developer index scripts.
- A leftover "Image Editor" permission has no screen behind it.

## 3. Remove the old shared password `Tango$`
The owner said "remove". It is still needed until the studio switches. No PC has Slate yet, so:
1. Install Slate Server 2.2.0.
2. In Recover Slate → Database passwords, switch to the studio's own password.
3. Delete `LEGACY_PASSWORD` and its fallback (`slate/core/infra/local_secrets.py`, `postgres_manager.py`, `db_engine.py`).
4. Only then rewrite git history to remove `Tango$`. This is a force-push: ask the owner first.

## 4. Open questions from the handbook writers (nothing exists yet)
- There is no leave report or leave export for HR.
- Withdrawal decisions and Revoke send no notification (approve and reject do).
- HR can correct their own attendance; nothing stops it.

## 5. Before rollout
- Test one real silent update on a spare PC: install by hand, publish a newer installer, choose Update now. Only stand-in installers have been tested so far.
- Run a one-week pilot with 3–5 real people (an artist, a supervisor, HR, production and IT) before the whole studio.
