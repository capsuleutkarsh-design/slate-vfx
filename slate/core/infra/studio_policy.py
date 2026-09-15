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
# everything else in DEFAULT_POLICY is a rule, not a preference.
SETTINGS_KEYS = {
    "late_cutoff": "late_cutoff",
    "standard_day_hours": "standard_day_hours",
    "weekly_offs": "weekly_offs",
    "accrual_days_per_month": "accrual_days_per_month",
    "carry_forward_cap": "carry_forward_cap",
    "sandwich_rule": "sandwich_rule",
    "comp_off_enabled": "comp_off_enabled",
}


def load_into_domain() -> dict:
    """Read what the studio has set and install it. Returns what was applied."""
    applied = {}
    try:
        from slate.core.infra.global_config import GlobalConfig
        for setting, rule in SETTINGS_KEYS.items():
            value = GlobalConfig.get(setting, None)
            if value not in (None, ""):
                applied[rule] = value
    except Exception as exc:
        logger.warning("Could not read the studio policy settings: %s", exc)
        return {}

    lp.set_overrides(applied)
    if applied:
        logger.info("Studio policy: %s", ", ".join(sorted(applied)))

    _load_bidding()
    return applied


def _load_bidding() -> None:
    """
    Hand the studio's bidding figures to the cost model.

    Same direction of dependency as the leave policy: the rules do not read
    settings, settings are pushed into the rules.
    """
    try:
        from slate.core.domain import bidding
        from slate.core.infra.config_manager import ConfigManager

        settings = ConfigManager().settings or {}
        supplied = settings.get("bidding")
        if isinstance(supplied, dict):
            bidding.set_overrides(supplied)
    except Exception as exc:
        logger.warning("Could not read the bidding settings: %s", exc)
