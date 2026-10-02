"""
One-off repairs of dashboard data (the DASHBOARD block of the registry).

clear_placeholder_thumbnails
    The dashboard's thumbnail loader used to write its placeholder picture
    ('placeholder_red.png' in one machine's install folder) into
    shot.thumbnail_path, and the next save stored it - 130 shots of one
    project pointed at a file on the developer's machine. Those paths are
    blanked; real thumbnails are left alone. Works on PostgreSQL and SQLite,
    keeps everything else in the row.
"""

import json
import logging

logger = logging.getLogger(__name__)


def is_placeholder_path(path) -> bool:
    text = str(path or "").strip().lower().replace("\\", "/")
    if not text:
        return False
    return "placeholder_red" in text or "placeholder_yellow" in text \
        or "vfx_dashboard_pro/cache/thumbnails" in text or "vfx_dashboard_pro/ui/cache" in text


def clear_placeholder_thumbnails(db) -> bool:
    rows = db.execute_query(
        "SELECT id, data_json FROM tracking_shots WHERE CAST(data_json AS TEXT) LIKE %s",
        ("%placeholder_%",), fetch="all")
    if rows is None:
        return False
    fixed = 0
    for row in rows:
        row = dict(row)
        raw = row.get("data_json")
        try:
            data = json.loads(raw) if isinstance(raw, str) else dict(raw or {})
        except (TypeError, ValueError):
            continue
        if not is_placeholder_path(data.get("thumbnail_path")):
            continue
        data["thumbnail_path"] = ""
        result = db.execute_update("UPDATE tracking_shots SET data_json = %s WHERE id = %s",
                                   (json.dumps(data, default=str), row["id"]))
        if not result:
            return False
        fixed += 1
    if fixed:
        logger.info("Cleared %d placeholder thumbnail path(s) from tracking_shots.", fixed)
    return True
