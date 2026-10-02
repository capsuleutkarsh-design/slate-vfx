"""
People by name, not by login.

Screens showed 'rahul.s', 'it.sana' and 'artist02' wherever a person was
meant - ticket owners, thread authors, "WS-PAINT-002 is out with artist02" -
while the tables beside them showed display names. Pickers listed raw
usernames in joining-date order, service accounts first, and people who had
already left. This is the one lookup every screen uses:

    from slate.core.domain import people
    people.display_name("rahul.s")            -> 'Rahul Sharma'
    people.label("rahul.s")                   -> 'Rahul Sharma (rahul.s)'
    people.people_for_picker()                -> [Person, ...] alphabetical by
                                                 name, no leavers, no service
                                                 accounts (admin, tester, or
                                                 is_service set)

A username nobody knows comes back as itself, so a name is never lost just
because the account was removed. The directory is read once and kept for a
minute; call people.refresh() after changing someone's name.

Usernames stay what is stored and compared - they are the identity. Display
names are only for showing.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from datetime import date, datetime
from typing import Dict, Iterable, List, Optional

logger = logging.getLogger(__name__)

# Accounts that are not people, when the database has no is_service column yet.
SERVICE_USERNAMES = frozenset({"admin", "tester"})


@dataclass(frozen=True)
class Person:
    username: str
    display_name: str = ""
    job_title: str = ""
    location: str = ""
    last_day: Optional[date] = None
    is_service: bool = False
    active: bool = True

    @property
    def name(self) -> str:
        """The name to show: the display name, or the username when there is none."""
        return (self.display_name or "").strip() or self.username

    @property
    def label(self) -> str:
        """'Rahul Sharma (rahul.s)' - for pickers, where two Rahuls must be told apart."""
        if self.name.lower() == self.username.lower():
            return self.username
        return f"{self.name} ({self.username})"

    def has_left(self, today: Optional[date] = None) -> bool:
        return bool(self.last_day and self.last_day < (today or date.today()))

    def sort_key(self):
        return (self.name.casefold(), self.username.casefold())


def _as_date(value) -> Optional[date]:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


class Directory:
    CACHE_SECONDS = 60.0
    _cache: Dict[int, tuple] = {}
    _lock = threading.RLock()

    def __init__(self, db=None):
        if db is None:
            from slate.core.infra.database_manager import database_manager
            db = database_manager
        self.db = db

    def _key(self) -> int:
        return id(getattr(self.db, "backend", self.db))

    def _optional_columns(self) -> List[str]:
        try:
            from slate.core.infra.migrations.workplace_schema import _column_exists
        except Exception:
            return []
        wanted = ("job_title", "location", "last_day", "is_service", "is_active", "active")
        return [c for c in wanted if _column_exists(self.db, "ut_users", c)]

    def people(self) -> Dict[str, Person]:
        """Everyone, keyed by lower-case username."""
        with self._lock:
            hit = self._cache.get(self._key())
            if hit and time.monotonic() - hit[0] < self.CACHE_SECONDS:
                return hit[1]
        extra = self._optional_columns()
        columns = ["username", "display_name"] + extra
        rows = self.db.execute_query(
            "SELECT %s FROM ut_users" % ", ".join(columns), fetch="all")
        if rows is None:
            # Refused (a column this database lacks). Show usernames rather
            # than nothing; do not cache, so a fixed schema is picked up.
            return {}
        found: Dict[str, Person] = {}
        for row in rows:
            row = dict(row)
            username = str(row.get("username") or "").strip()
            if not username:
                continue
            flag = row.get("is_service")
            service = (bool(int(flag)) if str(flag or "").strip().lstrip("-").isdigit() else bool(flag)) \
                if flag is not None else False
            service = service or username.lower() in SERVICE_USERNAMES
            active_raw = row.get("is_active", row.get("active"))
            active = True if active_raw is None else str(active_raw).strip().lower() not in ("0", "false", "f", "no", "")
            found[username.lower()] = Person(
                username=username,
                display_name=str(row.get("display_name") or "").strip(),
                job_title=str(row.get("job_title") or "").strip(),
                location=str(row.get("location") or "").strip(),
                last_day=_as_date(row.get("last_day")),
                is_service=service,
                active=active,
            )
        with self._lock:
            self._cache[self._key()] = (time.monotonic(), found)
        return found

    @classmethod
    def refresh(cls) -> None:
        with cls._lock:
            cls._cache.clear()


def refresh() -> None:
    """Forget the cached directory, after a name or a last day changed."""
    Directory.refresh()


def _lookup(username, db=None) -> Optional[Person]:
    key = str(username or "").strip().lower()
    if not key:
        return None
    try:
        return Directory(db).people().get(key)
    except Exception as exc:
        # An outage must not turn a name into an error in the middle of a
        # table: the username is shown instead, and the screen's own read
        # will report the outage.
        logger.debug("People directory unavailable: %s", exc)
        return None


def person(username, db=None) -> Optional[Person]:
    return _lookup(username, db)


def display_name(username, db=None, *, empty: str = "") -> str:
    """The person's name, or the username itself when nobody by that name is known."""
    text = str(username or "").strip()
    if not text:
        return empty
    found = _lookup(text, db)
    return found.name if found else text


def label(username, db=None) -> str:
    """'Rahul Sharma (rahul.s)' - the name with the login, for pickers and audits."""
    text = str(username or "").strip()
    found = _lookup(text, db)
    return found.label if found else text


def display_names(usernames: Iterable, db=None) -> Dict[str, str]:
    """Many at once: {username: name}."""
    try:
        everyone = Directory(db).people()
    except Exception:
        everyone = {}
    out = {}
    for username in usernames:
        text = str(username or "").strip()
        found = everyone.get(text.lower())
        out[username] = found.name if found else text
    return out


def people_for_picker(db=None, *, include_leavers: bool = False, include_service: bool = False,
                      include_inactive: bool = False, today: Optional[date] = None) -> List[Person]:
    """
    Who to offer when a person is being chosen: alphabetical by name; people
    who have left (last day passed), deactivated accounts and service
    accounts left out unless asked for.
    """
    today = today or date.today()
    chosen = []
    for p in Directory(db).people().values():
        if p.is_service and not include_service:
            continue
        if not p.active and not include_inactive:
            continue
        if p.has_left(today) and not include_leavers:
            continue
        chosen.append(p)
    return sorted(chosen, key=Person.sort_key)


def find(text: str, db=None, **filters) -> List[Person]:
    """People whose name or username contains text (case-insensitive), for type-ahead."""
    needle = str(text or "").strip().casefold()
    pool = people_for_picker(db, **filters)
    if not needle:
        return pool
    return [p for p in pool if needle in p.name.casefold() or needle in p.username.casefold()]
