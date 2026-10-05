"""Shared set-up for dashboard tests: the dashboard only saves into a project that exists."""

import json


def open_project(db, code, name=None):
    """Make sure tracking_projects has an active row for `code` (a save refuses a missing project)."""
    db.save_tracking_project(code, name or code, json.dumps({"code": code, "name": name or code}))
    return code


def person(db, username, job_title):
    """A ut_users row: a lead's department comes from their own record's job title."""
    db.execute_update("DELETE FROM ut_users WHERE username = %s", (username,))
    db.execute_update("INSERT INTO ut_users (username, job_title) VALUES (%s, %s)",
                      (username, job_title))
    return username
