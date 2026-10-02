"""
Choosing a person.

People were picked from plain combo boxes of raw usernames - 150 of them after
an import, in joining-date order, no display names, no search. One was an
editable combo whose value came from the item still selected rather than from
what was typed: typing "new.hire.typo" and pressing Start joining created a
checklist for krishna.chopra.

A PersonPicker shows "Display Name (username)", filters as you type (any part
of either name), lists active people only unless told otherwise, and gives
back a real username or nothing - text that matches nobody is refused, never
quietly turned into whoever was selected before.

    from slate.gui.components.person_picker import PersonPicker
    picker = PersonPicker(placeholder="Who is joining?")
    picker.person_changed.connect(self._on_person)      # username or ""
    ...
    username = picker.username()                        # "" when nothing valid
    picker.set_username("priya")
    picker.suggest("Priya S")                           # best match from a file's Name column
"""

from __future__ import annotations

import logging
from typing import Callable, Iterable, List, Optional, Tuple

from PySide6.QtCore import QStringListModel, Qt, Signal
from PySide6.QtWidgets import QComboBox, QCompleter

logger = logging.getLogger(__name__)

Person = Tuple[str, str]        # (username, display name)


def people(user_manager=None, include_inactive: bool = False,
           include: Callable[[str, dict], bool] = None) -> List[Tuple[str, str, dict]]:
    """
    [(username, display name, record)], alphabetical by name.

    By default the studio's people directory decides who is offered
    (slate.core.domain.people.people_for_picker): no service accounts, and
    nobody deactivated or past their last day unless include_inactive. A
    `user_manager` (anything with get_all_users) is a fixed list instead - an
    import dialog's snapshot, or a test.
    """
    if user_manager is None:
        records = _directory_records(include_inactive)
    else:
        try:
            records = user_manager.get_all_users() or {}
        except Exception as exc:
            logger.warning("People could not be read: %s", exc)
            records = {}
    result = []
    for username, record in records.items():
        record = record or {}
        if not include_inactive and not record.get("active", True):
            continue
        if include is not None and not include(username, record):
            continue
        display = str(record.get("display_name") or "").strip() or str(username)
        result.append((str(username), display, record))
    result.sort(key=lambda p: (p[1].casefold(), p[0].casefold()))
    return result


def _directory_records(include_inactive: bool) -> dict:
    """{username: record} from the people directory, in the shape get_all_users gives."""
    try:
        from slate.core.domain import people as directory
        chosen = directory.people_for_picker(include_leavers=include_inactive,
                                             include_inactive=include_inactive)
    except Exception as exc:
        logger.warning("People could not be read: %s", exc)
        return {}
    return {p.username: {"display_name": p.display_name, "job_title": p.job_title,
                         "location": p.location, "last_day": p.last_day,
                         "active": bool(p.active) and not p.has_left()}
            for p in chosen}


def label_for(username: str, display: str) -> str:
    display = (display or "").strip()
    if not display or display.casefold() == str(username).casefold():
        return str(username)
    return f"{display} ({username})"


class PersonPicker(QComboBox):
    """An editable, searchable choice of one person. See the module notes."""

    person_changed = Signal(str)          # username, or "" when nothing valid

    def __init__(self, user_manager=None, parent=None, *, include_inactive: bool = False,
                 include: Callable[[str, dict], bool] = None,
                 order: Callable[[Tuple[str, str, dict]], object] = None,
                 placeholder: str = "Type a name…", allow_empty: bool = True):
        super().__init__(parent)
        self._user_manager = user_manager
        self._include_inactive = include_inactive
        self._include = include
        self._order = order
        self._allow_empty = allow_empty
        self._last = None

        self.setEditable(True)
        self.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.setMaxVisibleItems(15)
        self.lineEdit().setPlaceholderText(placeholder)
        self.lineEdit().setClearButtonEnabled(True)

        completer = QCompleter(self)
        completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        completer.setFilterMode(Qt.MatchFlag.MatchContains)
        completer.setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
        completer.setMaxVisibleItems(15)
        self._completer_model = QStringListModel(self)
        completer.setModel(self._completer_model)
        self.setCompleter(completer)

        self.lineEdit().textEdited.connect(lambda _t: self._emit_if_changed())
        self.currentIndexChanged.connect(lambda _i: self._emit_if_changed())
        completer.activated.connect(lambda _t: self._emit_if_changed())
        self.reload()

    # ------------------------------------------------------------ contents
    def reload(self):
        """Read the people again (keeps the chosen person when still listed)."""
        keep = self.username()
        entries = people(self._user_manager, self._include_inactive, self._include)
        if self._order is not None:
            entries.sort(key=self._order)
        self.blockSignals(True)
        try:
            self.clear()
            for username, display, _record in entries:
                self.addItem(label_for(username, display), username)
                self.setItemData(self.count() - 1, display, Qt.ItemDataRole.ToolTipRole)
            self._completer_model.setStringList([self.itemText(i) for i in range(self.count())])
            self.setCurrentIndex(-1)
            self.lineEdit().clear()
        finally:
            self.blockSignals(False)
        if keep:
            self.set_username(keep)
        self._emit_if_changed()

    def usernames(self) -> List[str]:
        return [self.itemData(i) for i in range(self.count())]

    # ------------------------------------------------------------ the value
    def _match(self, text: str) -> str:
        """The username this text names, exactly and unambiguously; else ""."""
        wanted = (text or "").strip().casefold()
        if not wanted:
            return ""
        hits = set()
        for i in range(self.count()):
            username = str(self.itemData(i))
            label = self.itemText(i)
            display = str(self.itemData(i, Qt.ItemDataRole.ToolTipRole) or "")
            if wanted in (label.casefold(), username.casefold(), display.casefold()):
                hits.add(username)
        return hits.pop() if len(hits) == 1 else ""

    def username(self) -> str:
        """The chosen person's username, or "" when the text names nobody."""
        return self._match(self.currentText())

    def is_valid(self) -> bool:
        text = self.currentText().strip()
        return bool(self.username()) or (self._allow_empty and not text)

    def set_username(self, username: Optional[str]) -> bool:
        for i in range(self.count()):
            if str(self.itemData(i)).casefold() == str(username or "").casefold():
                self.setCurrentIndex(i)
                self._emit_if_changed()
                return True
        if not username:
            self.setCurrentIndex(-1)
            self.lineEdit().clear()
            self._emit_if_changed()
        return False

    def suggest(self, name: str) -> str:
        """
        Pick the best match for a name from elsewhere (an import file's Name
        column): exact username or display name first, then a single person
        whose name contains every word. Returns the username chosen, or "".
        """
        exact = self._match(name)
        if exact:
            self.set_username(exact)
            return exact
        words = [w for w in (name or "").casefold().split() if w]
        if not words:
            return ""
        hits = []
        for i in range(self.count()):
            haystack = self.itemText(i).casefold()
            if all(w in haystack for w in words):
                hits.append(str(self.itemData(i)))
        if len(hits) == 1:
            self.set_username(hits[0])
            return hits[0]
        return ""

    def _emit_if_changed(self):
        value = self.username()
        valid = self.is_valid()
        self.setProperty("invalid", not valid)
        self.setToolTip("" if valid else "No such person - pick a name from the list.")
        style = self.style()
        style.unpolish(self)
        style.polish(self)
        if value != self._last:
            self._last = value
            self.person_changed.emit(value)
