"""
Settings that belong to the studio, not to one machine.

"Studio policy" - when somebody counts as late, how long a day is - was saved
with GlobalConfig.set(), which writes this workstation's own
%LOCALAPPDATA%\\Slate\\config.json. Every machine could hold its own studio: an
artist who set "late after 23:59" was never late on their own screen while HR
saw something else, and the Settings note promised the opposite ("so they
cannot disagree"). Bidding figures lived in each person's settings file.

These live in the database now, in one table, so there is one answer for the
whole studio:

    studio_settings (key TEXT PRIMARY KEY, value TEXT (JSON), updated_by, updated_at)

    from slate.core.infra.studio_settings import get_setting, set_setting
    get_setting("currency")                       -> 'INR'
    get_setting("day_rates")                      -> {'INR': 8000, 'USD': 300}
    set_setting("gst_rate", 18, by="hr.priya")    -> WriteResult

Every key has a default (DEFAULTS) and a check (a bad value is refused with a
reason rather than stored). Reads are cached for a few seconds so a screen can
ask freely. Per-machine settings (paths, the theme, where the database is)
stay in GlobalConfig; only studio-wide ones belong here.

The area teams add their own keys by adding to DEFAULTS and VALIDATORS - or,
without touching this file, with register_key() from their own module.
"""

from __future__ import annotations

import copy
import json
import logging
import threading
import time
from datetime import datetime
from typing import Any, Callable, Dict, Optional

from .db_results import DatabaseUnavailableError, WriteResult

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------- schema

TABLE_PG = """
    CREATE TABLE IF NOT EXISTS studio_settings (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL DEFAULT 'null',
        updated_by TEXT DEFAULT '',
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )"""

TABLE_SQLITE = """
    CREATE TABLE IF NOT EXISTS studio_settings (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL DEFAULT 'null',
        updated_by TEXT DEFAULT '',
        updated_at TEXT DEFAULT (datetime('now'))
    )"""


# ------------------------------------------------------------------- defaults
#
# The FIX_PLAN decisions, as the values a studio starts with. Everything is
# editable; these only apply until somebody saves a value.

DEFAULTS: Dict[str, Any] = {
    # The studio's own currency. A bid may be in another (see money.py).
    "currency": "INR",
    # An artist day, per currency. No exchange rate is assumed: a currency
    # without a rate here has none until the studio sets one.
    "day_rates": {"INR": 8000, "USD": 300},
    # GST on rupee bids, percent. Foreign-currency bids start at 0.
    "gst_rate": 18,
    # Business hours, for SLA clocks and anything else that counts working
    # time. The working days are the studio policy's weekly offs
    # (attendance_policy); an older saved value may still carry 'days'.
    "working_hours": {"start": "10:00", "end": "19:00"},
    # The leave and attendance rules (see core/domain/leave_policy.py for what
    # each one means). Only the keys present override the rule's default.
    "attendance_policy": {},
    # The bidding cost model's figures (core/domain/bidding.py): multipliers,
    # day_rate, margin_percent. Empty means the model's defaults.
    "bidding": {},
    # The dashboard's pick lists.
    "shot_types": ["Prep", "2D Comp", "2.5D Comp", "CG Comp", "AI Shot", "Roto", "DMP"],
    "shot_priorities": [
        {"value": 0, "label": "Urgent"},
        {"value": 1, "label": "High"},
        {"value": 2, "label": "Normal"},
        {"value": 3, "label": "Low"},
    ],
}


def _is_time(text) -> bool:
    try:
        hour, minute = str(text).split(":")[:2]
        return 0 <= int(hour) <= 23 and 0 <= int(minute) <= 59
    except (TypeError, ValueError):
        return False


def _check_currency(value):
    from slate.core.domain.money import CURRENCIES
    code = str(value or "").strip().upper()
    if code not in CURRENCIES:
        raise ValueError("Currency must be one of " + ", ".join(CURRENCIES) + ".")
    return code


def _check_day_rates(value):
    from slate.core.domain.money import CURRENCIES, to_decimal
    if not isinstance(value, dict):
        raise ValueError("Day rates are a currency -> amount table.")
    out = {}
    for code, amount in value.items():
        code = str(code).strip().upper()
        if code not in CURRENCIES:
            raise ValueError(f"Unknown currency {code!r}.")
        if amount in (None, ""):
            continue
        number = to_decimal(amount)
        if number <= 0:
            raise ValueError(f"The {code} day rate must be more than zero.")
        # Stored as text so the exact figure survives JSON.
        out[code] = int(number) if number == number.to_integral_value() else str(number)
    return out


def _check_percent(value):
    number = float(value)
    if not 0 <= number <= 100:
        raise ValueError("A percentage must be between 0 and 100.")
    return int(number) if number.is_integer() else number


def _check_hours(value):
    if not isinstance(value, dict):
        raise ValueError("Working hours need a start, an end and the working days.")
    start, end = value.get("start"), value.get("end")
    if not (_is_time(start) and _is_time(end)):
        raise ValueError("Working hours are HH:MM on a 24-hour clock.")
    if str(start).zfill(5) >= str(end).zfill(5):
        raise ValueError("The working day must end after it starts.")
    out = {"start": str(start)[:5].zfill(5), "end": str(end)[:5].zfill(5)}
    # The working days are the studio policy's weekly offs now; 'days' is
    # only checked (and kept) when an older value still carries it.
    if "days" in value:
        days = sorted({int(d) for d in (value.get("days") or [])})
        if not days or any(d < 0 or d > 6 for d in days):
            raise ValueError("Pick at least one working day.")
        out["days"] = days
    return out


def _check_policy(value):
    if not isinstance(value, dict):
        raise ValueError("The attendance policy is a table of rules.")
    from slate.core.domain import leave_policy as lp
    known = set(lp.DEFAULT_POLICY)
    unknown = [k for k in value if k not in known]
    if unknown:
        raise ValueError("Unknown policy rule(s): " + ", ".join(sorted(unknown)))
    out = dict(value)
    if "late_cutoff" in out and not _is_time(out["late_cutoff"]):
        raise ValueError("'Late after' must be a time, HH:MM.")
    if "auto_logout_time" in out and not _is_time(out["auto_logout_time"]):
        raise ValueError("The auto punch-out time must be HH:MM.")
    if "standard_day_hours" in out:
        hours = float(out["standard_day_hours"])
        if not 1 <= hours <= 24:
            raise ValueError("A standard day is between 1 and 24 hours.")
        out["standard_day_hours"] = hours
    if "weekly_offs" in out:
        offs = sorted({int(d) for d in out["weekly_offs"] or []})
        if any(d < 0 or d > 6 for d in offs) or len(offs) > 6:
            raise ValueError("Weekly offs are days 0 (Monday) to 6 (Sunday), not all seven.")
        out["weekly_offs"] = offs
    for key in ("accrual_days_per_month", "carry_forward_cap", "comp_off_for_weekly_off",
                "comp_off_for_holiday", "comp_off_hours_half", "comp_off_hours_full"):
        if key in out:
            number = float(out[key])
            if number < 0:
                raise ValueError(f"{key.replace('_', ' ')} cannot be negative.")
            out[key] = number
    if "comp_off_expiry_days" in out:
        out["comp_off_expiry_days"] = max(int(out["comp_off_expiry_days"]), 0)
    for key in ("sandwich_rule", "comp_off_enabled", "project_rest_enabled"):
        if key in out:
            out[key] = bool(out[key])
    return out


def _check_names(value):
    names = [str(v).strip() for v in (value or []) if str(v).strip()]
    if not names:
        raise ValueError("The list cannot be empty.")
    seen, out = set(), []
    for name in names:
        if name.lower() not in seen:
            seen.add(name.lower())
            out.append(name)
    return out


def _check_priorities(value):
    out = []
    for item in value or []:
        if isinstance(item, dict):
            out.append({"value": int(item["value"]), "label": str(item.get("label") or item["value"]).strip()})
        else:
            out.append({"value": int(item), "label": str(item)})
    if not out:
        raise ValueError("There must be at least one priority.")
    return sorted(out, key=lambda p: p["value"])


def _check_bidding(value):
    if not isinstance(value, dict):
        raise ValueError("The bidding figures are a table.")
    return dict(value)


VALIDATORS: Dict[str, Callable[[Any], Any]] = {
    "currency": _check_currency,
    "day_rates": _check_day_rates,
    "gst_rate": _check_percent,
    "working_hours": _check_hours,
    "attendance_policy": _check_policy,
    "bidding": _check_bidding,
    "shot_types": _check_names,
    "shot_priorities": _check_priorities,
}


def register_key(key: str, default: Any, validator: Optional[Callable[[Any], Any]] = None) -> None:
    """
    Add a studio-wide setting from an area's own module:

        register_key("sla_first_response_hours", {"P1": 1, "P2": 4}, my_check)

    The validator gets the value and returns what to store, or raises
    ValueError with a sentence a person can act on.
    """
    DEFAULTS.setdefault(key, default)
    if validator is not None:
        VALIDATORS[key] = validator


# ------------------------------------------------------------------ the store

class StudioSettings:
    """Reads and writes studio_settings on one database handle."""

    CACHE_SECONDS = 15.0
    _cache: Dict[int, tuple] = {}
    _cache_lock = threading.RLock()

    def __init__(self, db=None):
        if db is None:
            from .database_manager import database_manager
            db = database_manager
        self.db = db

    # -- reading

    def _key(self) -> int:
        return id(getattr(self.db, "backend", self.db))

    def _load(self) -> Dict[str, Any]:
        with self._cache_lock:
            hit = self._cache.get(self._key())
            if hit and time.monotonic() - hit[0] < self.CACHE_SECONDS:
                return hit[1]
        rows = self.db.execute_query("SELECT key, value FROM studio_settings", fetch="all")
        values: Dict[str, Any] = {}
        if rows is None:
            # The table is not there yet (a database from before this
            # migration ran) - the defaults apply. Not cached, so the values
            # appear as soon as the table does.
            return values
        for row in rows:
            row = dict(row)
            try:
                values[row["key"]] = json.loads(row["value"]) if row.get("value") is not None else None
            except (TypeError, ValueError):
                logger.warning("Studio setting %s holds something that is not JSON; ignored.", row.get("key"))
        with self._cache_lock:
            self._cache[self._key()] = (time.monotonic(), values)
        return values

    def get(self, key: str, default: Any = None) -> Any:
        """The studio's value for key, else DEFAULTS[key], else default."""
        values = self._load()
        if key in values and values[key] is not None:
            return copy.deepcopy(values[key])
        if key in DEFAULTS:
            return copy.deepcopy(DEFAULTS[key])
        return default

    def is_set(self, key: str) -> bool:
        """Whether the studio has saved a value (rather than using the default)."""
        return self._load().get(key) is not None

    def all(self) -> Dict[str, Any]:
        """Every known setting with its effective value."""
        merged = copy.deepcopy(DEFAULTS)
        for key, value in self._load().items():
            if value is not None:
                merged[key] = copy.deepcopy(value)
        return merged

    def meta(self, key: str) -> Dict[str, Any]:
        """Who changed a setting last and when: {'updated_by': ..., 'updated_at': ...}."""
        row = self.db.execute_query(
            "SELECT updated_by, updated_at FROM studio_settings WHERE key = %s", (key,), fetch="one")
        return dict(row) if row else {}

    # -- writing

    def set(self, key: str, value: Any, by: str = "") -> WriteResult:
        """
        Save one setting for the whole studio. Returns the WriteResult; a
        value that fails its check is refused with the reason in .error and
        nothing is written.
        """
        check = VALIDATORS.get(key)
        try:
            clean = check(value) if check else value
            text = json.dumps(clean, sort_keys=True)
        except (TypeError, ValueError, KeyError) as exc:
            return WriteResult(False, error=str(exc) or "That value is not allowed.", kind="invalid")
        result = self.db.execute_update(
            "INSERT INTO studio_settings (key, value, updated_by, updated_at) "
            "VALUES (%s, %s, %s, %s) "
            "ON CONFLICT (key) DO UPDATE SET value = excluded.value, "
            "updated_by = excluded.updated_by, updated_at = excluded.updated_at",
            (key, text, str(by or ""), datetime.now().replace(microsecond=0)))
        self.invalidate()
        if result:
            logger.info("Studio setting %s changed by %s.", key, by or "someone")
        return result

    def set_many(self, values: Dict[str, Any], by: str = "") -> WriteResult:
        """Several settings, all checked first and then saved together."""
        clean = {}
        for key, value in values.items():
            check = VALIDATORS.get(key)
            try:
                clean[key] = json.dumps(check(value) if check else value, sort_keys=True)
            except (TypeError, ValueError, KeyError) as exc:
                return WriteResult(False, error=f"{key}: {exc}", kind="invalid")
        from .transaction import atomic
        stamp = datetime.now().replace(microsecond=0)
        try:
            with atomic(self.db) as tx:
                for key, text in clean.items():
                    tx.write(
                        "INSERT INTO studio_settings (key, value, updated_by, updated_at) "
                        "VALUES (%s, %s, %s, %s) "
                        "ON CONFLICT (key) DO UPDATE SET value = excluded.value, "
                        "updated_by = excluded.updated_by, updated_at = excluded.updated_at",
                        (key, text, str(by or ""), stamp))
        except DatabaseUnavailableError:
            raise
        except Exception as exc:
            self.invalidate()
            return WriteResult(False, error=str(exc))
        self.invalidate()
        return WriteResult(True, rows=len(clean))

    def reset(self, key: str) -> WriteResult:
        """Back to the default."""
        result = self.db.execute_update("DELETE FROM studio_settings WHERE key = %s", (key,))
        self.invalidate()
        return result

    @classmethod
    def invalidate(cls) -> None:
        with cls._cache_lock:
            cls._cache.clear()


# ------------------------------------------------------------------ shortcuts

def get_setting(key: str, default: Any = None, db=None) -> Any:
    """
    The studio-wide value. Never raises for a missing table or a refused read
    (the default is returned); an unreachable database still raises
    DatabaseUnavailableError so a screen can say so rather than show a
    default as if it were the studio's choice.
    """
    try:
        return StudioSettings(db).get(key, default)
    except DatabaseUnavailableError:
        raise
    except Exception as exc:
        logger.warning("Could not read studio setting %s: %s", key, exc)
        return copy.deepcopy(DEFAULTS.get(key, default))


def set_setting(key: str, value: Any, by: str = "", db=None) -> WriteResult:
    return StudioSettings(db).set(key, value, by=by)


def ensure_table(db) -> bool:
    """Create studio_settings if it is missing. Called by the migration registry."""
    mode = str(getattr(db, "active_mode", "") or "").lower()
    postgres = mode == "postgres" if mode else "postgres" in type(db).__name__.lower()
    return bool(db.execute_update(TABLE_PG if postgres else TABLE_SQLITE))
