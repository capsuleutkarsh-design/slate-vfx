"""Shared set-up for dashboard tests: the dashboard only saves into a project that exists."""

import json


def open_project(db, code, name=None):
    """Make sure tracking_projects has an active row for `code` (a save refuses a missing project)."""
    db.save_tracking_project(code, name or code, json.dumps({"code": code, "name": name or code}))
    return code
