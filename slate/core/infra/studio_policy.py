"""
The studio's own leave and attendance settings, handed to the rules.

``core/domain/leave_policy`` deliberately imports nothing but the standard
library: the rules have to be readable, and testable, without a database, a
config file or Qt anywhere near them. So the settings are pushed into it from
here rather than read out from there, which keeps the dependency pointing the
one way it should.

Call :func:`load_into_domain` once at start up, and again whenever somebody
saves the Settings tab, so a changed start time takes effect without a restart.
"""

from __future__ import annotations

import logging

from slate.core.domain import leave_policy as lp

logger = logging.getLogger(__name__)


# Settings key -> the policy value it sets. Only these are studio-editable;
# everything else in DEFAULT_POLICY is a rule, not a preference. These were
# per-machine GlobalConfig keys; they are only read from there now when the
# database cannot be reached (and once, at upgrade, to adopt them - see
# migrations/foundation_data.adopt_machine_settings).
SETTINGS_KEYS = {
    "late_cutoff": "late_cutoff",
    "standard_day_hours": "standard_day_hours",
    "weekly_offs": "weekly_offs",
    "accrual_days_per_month": "accrual_days_per_month",
    "carry_forward_cap": "carry_forward_cap",
    "sandwich_rule": "sandwich_rule",
    "comp_off_enabled": "comp_off_enabled",
}

# The rules a studio may change from Settings > Studio Policy, in the order
# they are shown. Anything else in leave_policy.DEFAULT_POLICY is a rule.
EDITABLE_RULES = (
    "late_cutoff", "standard_day_hours", "auto_logout_time", "weekly_offs",
    "accrual_days_per_month", "carry_forward_cap", "sandwich_rule",
    "comp_off_enabled", "comp_off_for_weekly_off", "comp_off_for_holiday",
    "comp_off_hours_half", "comp_off_hours_full", "comp_off_expiry_days",
    "project_rest_enabled",
)


def _machine_policy() -> dict:
    """This machine's old per-machine values - only for when the database is out of reach."""
    applied = {}
    try:
        from slate.core.infra.global_config import GlobalConfig
        for setting, rule in SETTINGS_KEYS.items():
            value = GlobalConfig.get(setting, None)
            if value not in (None, ""):
                applied[rule] = value
    except Exception as exc:
        logger.warning("Could not read this machine's policy settings: %s", exc)
    return applied


def studio_rules(db=None) -> dict:
    """What the studio has saved (the studio_settings row), without defaults."""
    from slate.core.infra.studio_settings import get_setting
    value = get_setting("attendance_policy", {}, db=db)
    return dict(value) if isinstance(value, dict) else {}


def save_rules(rules: dict, by: str = "", db=None):
    """
    Save the studio's rules for everybody and apply them here at once.
    Returns the WriteResult (a refused value comes back with .error).
    """
    from slate.core.infra.studio_settings import set_setting
    current = studio_rules(db)
    current.update({k: v for k, v in dict(rules or {}).items() if k in EDITABLE_RULES})
    result = set_setting("attendance_policy", current, by=by, db=db)
    if result:
        load_into_domain(db)
    return result


def load_into_domain(db=None) -> dict:
    """
    Read what the studio has set and install it. Returns what was applied.

    From the database, so every workstation applies the same rules. When the
    database cannot be reached, this machine's last known values are used so
    attendance still has a cutoff - and the log says so.
    """
    from slate.core.infra.db_results import DatabaseUnavailableError
    try:
        applied = studio_rules(db)
    except DatabaseUnavailableError:
        applied = _machine_policy()
        logger.warning("Studio policy read from this machine, the database is not reachable.")
    except Exception as exc:
        logger.warning("Could not read the studio policy: %s", exc)
        applied = _machine_policy()

    applied = {k: v for k, v in applied.items() if v not in (None, "")}
    lp.set_overrides(applied)
    if applied:
        logger.info("Studio policy: %s", ", ".join(sorted(applied)))

    _load_bidding(db)
    return applied


def _load_bidding(db=None) -> None:
    """
    Hand the studio's bidding figures to the cost model.

    Same direction of dependency as the leave policy: the rules do not read
    settings, settings are pushed into the rules. The figures are a studio
    setting now; they used to come from each person's own settings file.
    """
    try:
        from slate.core.domain import bidding
        from slate.core.infra.studio_settings import get_setting

        supplied = get_setting("bidding", {}, db=db)
        if isinstance(supplied, dict):
            bidding.set_overrides(supplied)
    except Exception as exc:
        logger.warning("Could not read the bidding settings: %s", exc)
