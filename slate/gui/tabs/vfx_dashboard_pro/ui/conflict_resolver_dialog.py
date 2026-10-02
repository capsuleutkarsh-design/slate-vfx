"""
What happens when somebody else saved a shot you have changed.

It used to be "Data Conflict Detected — Optimistic Concurrency Control" with
'local v3 vs db v4', no reel, nothing about which of your edits were at stake,
an unreadable cyan-on-blue Cancel, and a Force Overwrite that rewrote every
shot in the project.

Now each shot is named with its reel, who saved it and when, your edits on it
(old → new), and anything they changed in the same fields. The choices:

    Keep theirs, re-apply mine   (recommended) their save stays; your edits to
                                 fields they did not touch are put back on top,
                                 still unsaved, for you to check and save
    Overwrite with mine          (only for people allowed to force) your edits
                                 are written over theirs, for these shots only
    Cancel                       nothing changes; your edits stay pending
"""

from typing import List, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QTextBrowser, QVBoxLayout,
)

from slate.core.infra.gate import Gate
from slate.gui.core.controls import make_button, set_default_button
from slate.gui.core.icons import pixmap
from .date_fields import scaled_font


def _escape(text) -> str:
    return (str(text if text is not None else "")
            .replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


class ConflictResolverDialog(QDialog):
    """
    conflicts: [{"shot_name", "reel", "saved_by", "saved_at",
                 "mine": [(field, old, new)], "theirs": [(field, value)],
                 "overlap": [field]}]
    (a plain string is still accepted and shown as it is).
    """

    def __init__(self, conflicts, details: Optional[str] = None, can_force: bool = False, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Someone else saved these shots")
        self.setMinimumWidth(560)
        self.resize(640, 440)
        self.action_selected = "cancel"
        self.conflicts = conflicts if isinstance(conflicts, list) else []

        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        head = QHBoxLayout()
        icon = QLabel()
        icon.setPixmap(pixmap("alert", Gate.WARN, 28))
        head.addWidget(icon, 0, Qt.AlignmentFlag.AlignTop)
        title = QLabel(self._title())
        font = title.font()
        font = scaled_font(font, 1.25)
        font.setBold(True)
        title.setFont(font)
        title.setWordWrap(True)
        head.addWidget(title, 1)
        layout.addLayout(head)

        intro = QLabel(
            "They were saved by someone else after you opened them. Nothing of yours has "
            "been written yet, and nothing is lost whichever you choose.")
        intro.setWordWrap(True)
        intro.setStyleSheet(f"color: {Gate.TEXT_2};")
        layout.addWidget(intro)

        self.detail_box = QTextBrowser()
        self.detail_box.setOpenLinks(False)
        if self.conflicts:
            self.detail_box.setHtml(self._html())
        else:
            text = str(conflicts or "")
            if details:
                text += f"\n\n{details}"
            self.detail_box.setPlainText(text)
        layout.addWidget(self.detail_box, 1)

        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        self.btn_cancel = make_button("Cancel", "secondary",
                                      tooltip="Change nothing; your edits stay waiting to be saved",
                                      on_click=self.reject)
        buttons.addWidget(self.btn_cancel)
        buttons.addStretch(1)
        self.btn_force = None
        if can_force:
            self.btn_force = make_button(
                "Overwrite with mine", "danger",
                tooltip="Write your edits over theirs, for these shots only",
                on_click=self._on_force)
            buttons.addWidget(self.btn_force)
        self.btn_refresh = make_button(
            "Keep theirs, re-apply mine", "primary",
            tooltip="Load their save and put your other edits back on top, unsaved, to check",
            on_click=self._on_reload)
        buttons.addWidget(self.btn_refresh)
        layout.addLayout(buttons)
        set_default_button(self, self.btn_refresh)

    def _title(self) -> str:
        count = len(self.conflicts)
        if count == 1:
            c = self.conflicts[0]
            reel = f" ({c.get('reel')})" if c.get("reel") else ""
            return f"Someone else saved {c.get('shot_name', 'this shot')}{reel}"
        if count:
            return f"Someone else saved {count} of the shots you changed"
        return "Someone else saved these shots"

    def _html(self) -> str:
        parts = []
        for c in self.conflicts:
            reel = f" ({_escape(c.get('reel'))})" if c.get("reel") else ""
            who = c.get("saved_by") or ""
            when = c.get("saved_at") or ""
            byline = ""
            if who or when:
                byline = " - saved" + (f" by {_escape(who)}" if who else "") + (f" at {_escape(when)}" if when else "")
            parts.append(f"<p><b>{_escape(c.get('shot_name'))}{reel}</b>"
                         f"<span style='color:{Gate.TEXT_2}'>{byline}</span></p>")
            overlap = set(c.get("overlap") or [])
            mine = c.get("mine") or []
            if mine:
                items = []
                for field, old, new in mine:
                    mark = (f" <span style='color:{Gate.WARN}'>(they changed this too)</span>"
                            if field in overlap else "")
                    items.append(f"<li>{_escape(field)}: {_escape(old)} → {_escape(new)}{mark}</li>")
                parts.append(f"<p style='margin:0'>Your edits:</p><ul style='margin-top:0'>{''.join(items)}</ul>")
            theirs = c.get("theirs") or []
            if theirs:
                items = "".join(f"<li>{_escape(f)}: now {_escape(v)}</li>" for f, v in theirs)
                parts.append(f"<p style='margin:0'>Their changes:</p><ul style='margin-top:0'>{items}</ul>")
        return "".join(parts)

    def _on_reload(self):
        self.action_selected = "reload"
        self.accept()

    def _on_force(self):
        self.action_selected = "force"
        self.accept()
