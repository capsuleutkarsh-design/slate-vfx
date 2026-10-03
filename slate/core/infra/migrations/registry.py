"""
Every schema step Slate runs at start-up, in order, in one list.

DatabaseManager used to call each migration module by hand, one try/except
block per module, and a new table meant editing that constructor. With eight
area teams each needing tables and columns, that is eight people editing the
same few lines. So the steps are listed here instead, and run by
run_migrations(); DatabaseManager only calls that.

Adding a step for an area (the one line)
----------------------------------------
Write a module in this folder with a function that takes the database handle
and brings its tables up to date - on both backends, safely repeatable
(CREATE TABLE IF NOT EXISTS, add a column only when it is missing; see
workplace_schema.py for the helpers and the pattern). Then add ONE line to
your area's block below:

    PRODUCTION = (
        step("production_schema", "slate.core.infra.migrations.production_schema:apply_migration"),
    )

Nothing else to edit. Steps run in the order of the blocks and, within a
block, the order of the lines. A step that fails is logged and the others
still run - one area's missing column must not stop the whole studio
starting.

once=True is for data repairs that only need to happen one time (rewriting
old rows into a new shape). The registry records them in slate_migrations
and does not run them again. Schema steps should stay repeatable and
leave once at False.

The module path is a string, imported when it runs. That is deliberate: the
build collects every module in this package (deployment/Slate.spec,
collect_submodules('slate.core.infra.migrations')), so a new module here is
in the installed build without touching the spec.
"""

from __future__ import annotations

import importlib
import logging
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Step:
    name: str
    target: str            # "package.module:function"
    once: bool = False     # a one-off data repair, recorded in slate_migrations

    def resolve(self) -> Callable:
        module_name, _, func_name = self.target.partition(":")
        module = importlib.import_module(module_name)
        return getattr(module, func_name or "apply_migration")


def step(name: str, target: str, once: bool = False) -> Step:
    return Step(name, target, once)


# ------------------------------------------------------------------ the list
#
# Core steps first: every area's tables may rely on these. Keep the areas'
# blocks separate, with a blank line between them, so two teams adding a line
# each never edit the same lines.

CORE = (
    # Widening the shot key is safe on existing data, so there is no gate on it.
    step("shot_identity", "slate.core.infra.migrations.shot_identity:ensure_shot_identity"),
    # The stock library's indexes. Cheap to check, and without them every
    # browse of a large library sorts the whole table.
    step("stock_indexes", "slate.core.infra.migrations.stock_indexes:apply_migration"),
    # The HR and IT tables. Additive only.
    step("workplace_schema", "slate.core.infra.migrations.workplace_schema:apply_migration"),
    # Delivery packages - used to be created only when the delivery screen
    # was first opened.
    step("delivery_batches", "slate.core.infra.migrations.delivery_batches:apply_migration"),
    # Studio settings, change history columns, attendance metadata as JSONB,
    # bid currency, the service-account flag. See foundation_data.py.
    step("foundation_data", "slate.core.infra.migrations.foundation_data:apply_migration"),
    # One-off repairs of data written wrongly before the fixes above.
    step("repair_attendance_metadata",
         "slate.core.infra.migrations.foundation_data:repair_attendance_metadata", once=True),
    step("repair_tracking_columns",
         "slate.core.infra.migrations.foundation_data:repair_tracking_columns", once=True),
    step("backfill_change_history",
         "slate.core.infra.migrations.foundation_data:backfill_change_history", once=True),
    step("studio_settings_from_this_machine",
         "slate.core.infra.migrations.foundation_data:adopt_machine_settings", once=True),
)

# ---- production (Scheduling, Bidding) ----
PRODUCTION = (
    # Milestone owners and real dates, bid line items, revisions and tax.
    step("production_schema", "slate.core.infra.migrations.production_schema:apply_migration"),
    # Budgets float4 had rounded, put back to the exact figure.
    step("repair_bid_budgets",
         "slate.core.infra.migrations.production_schema:repair_bid_budgets", once=True),
)

# ---- people (Attendance, Leave, HR) ----
PEOPLE = (
    # Stage notes, half-day part, withdrawals of approved leave, who ticked a
    # joining/leaving line. See people_schema.py.
    step("people_schema", "slate.core.infra.migrations.people_schema:apply_migration"),
    step("people_split_decision_notes",
         "slate.core.infra.migrations.people_schema:split_decision_notes", once=True),
    step("people_normalise_employment",
         "slate.core.infra.migrations.people_schema:normalise_employment", once=True),
    step("people_clear_corrected_flags",
         "slate.core.infra.migrations.people_schema:clear_corrected_flags", once=True),
)

# ---- it (Hardware, Licences, Service desk, Deployment) ----
IT = (
    step("it_schema", "slate.core.infra.migrations.it_schema:apply_migration"),
    step("it_normalise_ticket_values",
         "slate.core.infra.migrations.it_schema:normalise_ticket_values", once=True),
    step("it_normalise_hardware",
         "slate.core.infra.migrations.it_schema:normalise_hardware", once=True),
    step("it_blank_junk_hardware_values",
         "slate.core.infra.migrations.it_schema:blank_junk_hardware_values", once=True),
    step("it_adopt_legacy_licences",
         "slate.core.infra.migrations.it_schema:adopt_legacy_licences", once=True),
)

# ---- system (Settings, Data Center, Audit logs, Live Ops) ----
SYSTEM = (
    # The studio's week is the policy's weekly offs; working days saved on
    # the money card are carried over. See system_settings.py.
    step("system_one_working_week",
         "slate.core.infra.migrations.system_settings:one_working_week", once=True),
)

# ---- dashboard (VFX Dashboard, versions, deliveries) ----
DASHBOARD = (
    step("dashboard_placeholder_thumbnails",
         "slate.core.infra.migrations.dashboard_repairs:clear_placeholder_thumbnails", once=True),
)

# ---- media (Stock, review, players) ----
MEDIA = (
    # Stock library: categories, sequences, visual tags, search text, soft
    # delete, favourites, studio picks, ingest roots. See media_schema.py.
    step("media_schema", "slate.core.infra.migrations.media_schema:apply_migration"),
    step("repair_stock_library",
         "slate.core.infra.migrations.media_schema:repair_stock_library", once=True),
)

# ---- ingest (Ingest, CAP Rename, Timeline) ----
INGEST = (
)

# ---- shell (main window, Home, header) ----
SHELL = (
)

# Last: the change feed installs triggers on tables the steps above create.
LAST = (
    step("change_feed", "slate.core.infra.migrations.change_feed:apply_migration"),
)


def all_steps() -> List[Step]:
    return list(CORE + PRODUCTION + PEOPLE + IT + SYSTEM + DASHBOARD + MEDIA
                + INGEST + SHELL + LAST)


# ------------------------------------------------------------------ running

LEDGER_PG = """
    CREATE TABLE IF NOT EXISTS slate_migrations (
        name TEXT PRIMARY KEY,
        applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        detail TEXT DEFAULT ''
    )"""

LEDGER_SQLITE = """
    CREATE TABLE IF NOT EXISTS slate_migrations (
        name TEXT PRIMARY KEY,
        applied_at TEXT DEFAULT (datetime('now')),
        detail TEXT DEFAULT ''
    )"""


def is_postgres(db) -> bool:
    mode = str(getattr(db, "active_mode", "") or "").lower()
    if mode:
        return mode == "postgres"
    return "postgres" in type(db).__name__.lower()


def _ledger_ready(db) -> bool:
    return bool(db.execute_update(LEDGER_PG if is_postgres(db) else LEDGER_SQLITE))


def already_applied(db, name: str) -> bool:
    row = db.execute_query("SELECT 1 AS done FROM slate_migrations WHERE name = %s",
                           (name,), fetch="one")
    return bool(row)


def mark_applied(db, name: str, detail: str = "") -> None:
    db.execute_update(
        "INSERT INTO slate_migrations (name, applied_at, detail) VALUES (%s, %s, %s) "
        "ON CONFLICT (name) DO NOTHING",
        (name, datetime.now().replace(microsecond=0), str(detail or "")[:500]))


def run_migrations(db, steps: Optional[List[Step]] = None) -> Dict[str, Tuple[str, float]]:
    """
    Run every step against this database handle. Returns
    {name: (outcome, seconds)} where outcome is "ok", "failed", "skipped"
    (a once-step already done) or "error: ..." - for the log and for tests.

    Never raises for a failing step. DatabaseUnavailableError from a step is
    logged like any other failure: start-up goes on and the screens report
    the outage themselves.
    """
    report: Dict[str, Tuple[str, float]] = {}
    if db is None:
        return report
    try:
        ledger = _ledger_ready(db)
    except Exception as exc:
        logger.error("Could not create the migration ledger: %s", exc)
        ledger = False

    for item in (steps if steps is not None else all_steps()):
        started = time.monotonic()
        try:
            if item.once:
                if not ledger:
                    report[item.name] = ("skipped: no ledger", 0.0)
                    continue
                if already_applied(db, item.name):
                    report[item.name] = ("skipped", 0.0)
                    continue
            outcome = item.resolve()(db)
            ok = outcome is not False
            if item.once and ok:
                mark_applied(db, item.name, "" if outcome in (True, None) else str(outcome))
            report[item.name] = ("ok" if ok else "failed", time.monotonic() - started)
            if not ok:
                logger.error("Migration step %s reported failure.", item.name)
        except Exception as exc:
            report[item.name] = (f"error: {exc}", time.monotonic() - started)
            logger.error("Migration step %s failed: %s", item.name, exc, exc_info=True)
    slow = [f"{name} {secs:.1f}s" for name, (_, secs) in report.items() if secs > 2.0]
    if slow:
        logger.info("Slow migration steps: %s", ", ".join(slow))
    return report
