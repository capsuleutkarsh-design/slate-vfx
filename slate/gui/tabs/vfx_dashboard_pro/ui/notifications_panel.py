"""
Unread notifications, in the tab people actually work in.

Notifications were only rendered in the Shot Review tab, so a new-scan alert on
an artist's own shot landed somewhere they might not open all week. This puts
the same messages in front of whoever is looking at the dashboard.
"""

import logging

from PySide6.QtCore import Signal

from slate.gui.components.notification_center import (
    NotificationsDialog as _SharedNotificationsDialog,
)


def unread_for(notifier, user_ids):
    """
    Every unread notification for this person, newest first.

    A person can be addressed by more than one identifier - a username, a
    display name, a numeric id - so all of them are collected and merged.
    """
    if not notifier:
        return []

    merged = {}
    for user_id in user_ids or []:
        if not user_id:
            continue
        try:
            for note in notifier.get_unread(user_id) or []:
                key = note.get("id")
                if key is not None:
                    merged[key] = note
        except Exception as exc:
            logging.debug("Could not read notifications for %s: %s", user_id, exc)

    return sorted(merged.values(),
                  key=lambda n: str(n.get("timestamp") or ""), reverse=True)


class NotificationsDialog(_SharedNotificationsDialog):
    """
    The shared notifications list (friendly times, Close, an empty state and
    "Mark all read" only when something is unread), fed with this tab's notes.

    Only used when the dashboard runs outside the main window; inside it the
    header's bell opens the same list.
    """

    shot_requested = Signal(str)

    def __init__(self, notes, notifier=None, parent=None):
        self.notifier = notifier
        ids = [n.get("id") for n in (notes or []) if n.get("id") is not None]

        def mark_all():
            if notifier is not None and ids:
                notifier.mark_read(ids)

        super().__init__(notes, on_mark_all=mark_all, parent=parent)
