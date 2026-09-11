"""
Generate a quick telemetry summary from local JSONL logs.

Usage:
    poetry run python scripts/telemetry_report.py --month 2026-04
"""

from __future__ import annotations

import argparse
import json
from typing import Any, Dict

from ut_vfx.core.infra.telemetry import telemetry


def _format_summary(summary: Dict[str, Any]) -> str:
    lines = [
        f"Month           : {summary.get('month', '')}",
        f"Total events    : {summary.get('total_events', 0)}",
        f"Sessions        : {summary.get('sessions', 0)}",
        f"Latest timestamp: {summary.get('latest_timestamp', '') or 'N/A'}",
        "",
        "Top events:",
    ]
    top_events = summary.get("top_events", [])
    if not top_events:
        lines.append("  (none)")
    else:
        for item in top_events:
            lines.append(f"  - {item.get('event', 'unknown')}: {item.get('count', 0)}")

    lines.append("")
    lines.append("Events by day:")
    by_day = summary.get("events_by_day", {})
    if not by_day:
        lines.append("  (none)")
    else:
        for day, count in by_day.items():
            lines.append(f"  - {day}: {count}")

    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Print telemetry summary from local log files.")
    parser.add_argument("--month", default="", help="Target month in YYYY-MM format.")
    parser.add_argument("--limit", type=int, default=5000, help="Maximum events to read from log.")
    parser.add_argument("--json", action="store_true", help="Output machine-readable JSON.")
    args = parser.parse_args()

    summary = telemetry.summarize_events(month=args.month or None, limit=args.limit)
    if args.json:
        print(json.dumps(summary, indent=2))
    else:
        print(_format_summary(summary))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

