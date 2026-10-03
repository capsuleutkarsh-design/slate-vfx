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


def unrecorded_actual_days(db) -> bool:
    """
    Every dashboard save used to write actual_days = 0 for every department,
    so "not recorded" (NULL) could not exist and Bidding showed 0 actual days
    for everything. Those zeros are made NULL again; real numbers stay.
    """
    from .workplace_schema import _column_exists
    if not _column_exists(db, "tracking_tasks", "actual_days"):
        return True
    return bool(db.execute_update("UPDATE tracking_tasks SET actual_days = NULL WHERE actual_days = 0"))


def fill_shot_artist_from_comp(db) -> bool:
    """
    The shot's artist used to be filled in from the Comp department every time
    a project was read. That no longer happens (the two are independent), so
    shots that only ever showed an artist that way get it written down once -
    what everybody sees stays the same.
    """
    from .workplace_schema import _column_exists
    artist = "artist" if _column_exists(db, "tracking_tasks", "artist") else "artist_name"
    rows = db.execute_query(
        f"SELECT s.id, s.data_json, t.{artist} AS comp_artist FROM tracking_shots s "
        f"JOIN tracking_tasks t ON t.shot_id = s.id AND t.department = 'comp' "
        f"WHERE COALESCE(t.{artist}, '') <> ''", fetch="all")
    if rows is None:
        return False
    for row in rows:
        row = dict(row)
        raw = row.get("data_json")
        try:
            data = json.loads(raw) if isinstance(raw, str) else dict(raw or {})
        except (TypeError, ValueError):
            continue
        if str(data.get("assigned_artist") or "").strip():
            continue
        data["assigned_artist"] = row["comp_artist"]
        if not db.execute_update("UPDATE tracking_shots SET data_json = %s WHERE id = %s",
                                 (json.dumps(data, default=str), row["id"])):
            return False
    return True
