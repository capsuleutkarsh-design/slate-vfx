"""
Versions and the notes attached to them.

A shot used to carry two strings - "previous version" and "current version" -
and nothing else. So when v003 went to the client, came back with notes, and
v004 was sent, the software remembered none of it. That history lived in email
and in a coordinator's memory.

A version here is a real record: what was sent, when, to whom, by whom, and
what came back. Notes hang off the version they were given on, which is what
makes them useful a month later.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import List, Optional


# Where a version went.
SENT_INTERNAL = "internal"
SENT_CLIENT = "client"
SENT_DIRECTOR = "director"
SENT_TO_CHOICES = ["", SENT_INTERNAL, SENT_CLIENT, SENT_DIRECTOR]

# What happened to it.
STATUS_PENDING = "Pending"
STATUS_IN_REVIEW = "In Review"
STATUS_APPROVED = "Approved"
STATUS_RETAKE = "Retake"
STATUS_CHOICES = [STATUS_PENDING, STATUS_IN_REVIEW, STATUS_APPROVED, STATUS_RETAKE]

# Statuses that still need somebody to look at them.
AWAITING_REVIEW = {STATUS_PENDING, STATUS_IN_REVIEW}

_VERSION_NUMBER = re.compile(r"(\d+)")


@dataclass
class VersionNote:
    id: int = -1
    version_id: int = -1
    source: str = SENT_CLIENT
    author: str = ""
    note_date: str = ""
    text: str = ""

    @classmethod
    def from_row(cls, row: dict) -> "VersionNote":
        return cls(
            id=int(row.get("id") or -1),
            version_id=int(row.get("version_id") or -1),
            source=str(row.get("source") or ""),
            author=str(row.get("author") or ""),
            note_date=str(row.get("note_date") or ""),
            text=str(row.get("text") or ""),
        )


@dataclass
class Version:
    id: int = -1
    project_code: str = ""
    shot_name: str = ""
    version_name: str = ""
    department: str = ""
    artist: str = ""
    status: str = STATUS_PENDING
    sent_to: str = ""
    sent_date: str = ""
    media_path: str = ""
    comment: str = ""
    created_by: str = ""
    created_at: str = ""
    notes: List[VersionNote] = field(default_factory=list)

    @property
    def number(self) -> int:
        """Numeric part of the version name, for ordering. v012 -> 12."""
        match = _VERSION_NUMBER.search(self.version_name or "")
        return int(match.group(1)) if match else 0

    @property
    def awaiting_review(self) -> bool:
        return self.status in AWAITING_REVIEW

    @classmethod
    def from_row(cls, row: dict) -> "Version":
        return cls(
            id=int(row.get("id") or -1),
            project_code=str(row.get("project_code") or ""),
            shot_name=str(row.get("shot_name") or ""),
            version_name=str(row.get("version_name") or ""),
            department=str(row.get("department") or ""),
            artist=str(row.get("artist") or ""),
            status=str(row.get("status") or STATUS_PENDING),
            sent_to=str(row.get("sent_to") or ""),
            sent_date=str(row.get("sent_date") or ""),
            media_path=str(row.get("media_path") or ""),
            comment=str(row.get("comment") or ""),
            created_by=str(row.get("created_by") or ""),
            created_at=str(row.get("created_at") or ""),
        )


def next_version_name(existing: List[str], prefix: str = "v",
                      width: int = 3) -> str:
    """
    Suggest the next version name from those already used.

    ['v001', 'v002'] -> 'v003'. An empty list starts at v001.
    """
    highest = 0
    for name in existing or []:
        match = _VERSION_NUMBER.search(str(name or ""))
        if match:
            highest = max(highest, int(match.group(1)))
    return f"{prefix}{highest + 1:0{width}d}"


class VersionStore:
    """Reads and writes versions. Uses the generic query API, so it works on
    both the Postgres and the SQLite backend."""

    def __init__(self, db=None):
        if db is None:
            from slate.core.infra.database_manager import database_manager
            db = database_manager
        self.db = db

    # -- reading -------------------------------------------------------
    def list_for_shot(self, project_code: str, shot_name: str,
                      with_notes: bool = True) -> List[Version]:
        """Every version of one shot, newest first."""
        try:
            rows = self.db.execute_query(
                "SELECT * FROM tracking_versions "
                "WHERE project_code=%s AND shot_name=%s",
                (project_code, shot_name), fetch="all",
            ) or []
        except Exception as exc:
            logging.exception("Could not read versions for %s: %s", shot_name, exc)
            return []

        versions = [Version.from_row(dict(row)) for row in rows]
        versions.sort(key=lambda v: (v.number, v.version_name), reverse=True)

        if with_notes:
            for version in versions:
                version.notes = self.notes_for_version(version.id)
        return versions

    def latest_for_shot(self, project_code: str,
                        shot_name: str) -> Optional[Version]:
        versions = self.list_for_shot(project_code, shot_name, with_notes=False)
        return versions[0] if versions else None

    def notes_for_version(self, version_id: int) -> List[VersionNote]:
        if version_id is None or int(version_id) < 0:
            return []
        try:
            rows = self.db.execute_query(
                "SELECT * FROM tracking_version_notes WHERE version_id=%s",
                (int(version_id),), fetch="all",
            ) or []
        except Exception as exc:
            logging.exception("Could not read notes for version %s: %s",
                              version_id, exc)
            return []

        notes = [VersionNote.from_row(dict(row)) for row in rows]
        notes.sort(key=lambda n: (n.note_date or "", n.id))
        return notes

    def awaiting_review(self, project_code: str) -> List[Version]:
        """Versions somebody still needs to look at, oldest submission first."""
        try:
            rows = self.db.execute_query(
                "SELECT * FROM tracking_versions WHERE project_code=%s",
                (project_code,), fetch="all",
            ) or []
        except Exception as exc:
            logging.exception("Could not read review queue: %s", exc)
            return []

        pending = [v for v in (Version.from_row(dict(r)) for r in rows)
                   if v.awaiting_review]
        pending.sort(key=lambda v: (v.sent_date or "9999", v.shot_name))
        return pending

    # -- writing -------------------------------------------------------
    def add_version(self, project_code: str, shot_name: str,
                    version_name: str = "", department: str = "",
                    artist: str = "", status: str = STATUS_PENDING,
                    sent_to: str = "", sent_date: str = "",
                    media_path: str = "", comment: str = "",
                    created_by: str = "") -> Optional[Version]:
        """Record a new version. Returns None if it could not be written."""
        project_code = str(project_code or "").strip()
        shot_name = str(shot_name or "").strip()
        if not project_code or not shot_name:
            return None

        if not version_name:
            existing = [v.version_name for v in
                        self.list_for_shot(project_code, shot_name,
                                           with_notes=False)]
            version_name = next_version_name(existing)

        if sent_to and not sent_date:
            sent_date = date.today().isoformat()

        try:
            ok = self.db.execute_update(
                "INSERT INTO tracking_versions "
                "(project_code, shot_name, version_name, department, artist, "
                " status, sent_to, sent_date, media_path, comment, created_by) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (project_code, shot_name, version_name, department, artist,
                 status, sent_to, sent_date, media_path, comment, created_by),
            )
            if ok is False:
                return None
        except Exception as exc:
            logging.exception("Could not add version %s for %s: %s",
                              version_name, shot_name, exc)
            return None

        for version in self.list_for_shot(project_code, shot_name,
                                          with_notes=False):
            if version.version_name == version_name:
                return version
        return None

    def update_version(self, version_id: int, **fields) -> bool:
        """Change one or more fields on a version."""
        allowed = {"department", "artist", "status", "sent_to", "sent_date",
                   "media_path", "comment"}
        updates = {k: v for k, v in fields.items() if k in allowed}
        if not updates or version_id is None or int(version_id) < 0:
            return False

        # Marking a version as sent stamps the date if none was given.
        if updates.get("sent_to") and not updates.get("sent_date"):
            updates["sent_date"] = date.today().isoformat()

        assignments = ", ".join(f"{key}=%s" for key in updates)
        params = tuple(updates.values()) + (int(version_id),)
        try:
            return bool(self.db.execute_update(
                f"UPDATE tracking_versions SET {assignments} WHERE id=%s",
                params,
            ))
        except Exception as exc:
            logging.exception("Could not update version %s: %s", version_id, exc)
            return False

    def add_note(self, version_id: int, text: str, source: str = SENT_CLIENT,
                 author: str = "", note_date: str = "") -> bool:
        """Attach feedback to a specific version."""
        text = str(text or "").strip()
        if not text or version_id is None or int(version_id) < 0:
            return False

        try:
            return bool(self.db.execute_update(
                "INSERT INTO tracking_version_notes "
                "(version_id, source, author, note_date, text) "
                "VALUES (%s, %s, %s, %s, %s)",
                (int(version_id), source, author,
                 note_date or date.today().isoformat(), text),
            ))
        except Exception as exc:
            logging.exception("Could not add note to version %s: %s",
                              version_id, exc)
            return False

    def delete_version(self, version_id: int) -> bool:
        if version_id is None or int(version_id) < 0:
            return False
        try:
            self.db.execute_update(
                "DELETE FROM tracking_version_notes WHERE version_id=%s",
                (int(version_id),),
            )
            return bool(self.db.execute_update(
                "DELETE FROM tracking_versions WHERE id=%s", (int(version_id),)
            ))
        except Exception as exc:
            logging.exception("Could not delete version %s: %s", version_id, exc)
            return False
