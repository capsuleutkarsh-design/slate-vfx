"""
Recovery: for when nobody can sign in.

The same page sits in the Slate Server window and in the standalone
"Recover Slate" window (which works when the server window will not start).
It drives slate_server.core.recovery.session.RecoverySession, exactly as the
command line does.

    Health check        read-only, no key needed
    Unlock              the Recovery Key - this PC only
    Accounts            reset a password / create or restore an administrator
    Database passwords  the workstations' one and the superuser's, separately
    Security switches   turn them off (works with no database at all), or one at a
                        time to log only / on, each checked first
    Snapshots           put the last automatic copy back
    Recovery Key        make a new one (old key, or Windows administrator)

Anything that changes something runs off the screen's thread, so the window
stays responsive while the database is started or a snapshot is taken.
"""

from __future__ import annotations

import logging
from typing import Callable, Optional

from PySide6.QtCore import QObject, Qt, QThread, Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QFileDialog, QHBoxLayout,
                               QLabel, QLineEdit, QMessageBox, QPlainTextEdit, QPushButton,
                               QVBoxLayout, QWidget, QApplication)

from ..design_system import C, T
from .operations_view import _button, _heading, _panel

logger = logging.getLogger(__name__)


def _field(placeholder: str, secret: bool = False) -> QLineEdit:
    edit = QLineEdit()
    edit.setPlaceholderText(placeholder)
    if secret:
        edit.setEchoMode(QLineEdit.EchoMode.Password)
    edit.setStyleSheet(
        f"background: {C.BG_ROOT}; color: {C.TEXT_PRIMARY}; border: 1px solid "
        f"{C.BORDER_DEFAULT}; border-radius: 4px; padding: 6px;")
    edit.setMinimumHeight(32)
    return edit


def _note(text: str) -> QLabel:
    label = QLabel(text)
    label.setWordWrap(True)
    label.setStyleSheet(f"font-size: 12px; color: {C.TEXT_SECONDARY}; background: transparent; "
                        f"border: none;")
    return label


class _Job(QObject):
    """One action off the screen's thread. said() carries progress lines."""
    said = Signal(str)
    done = Signal(object, str)          # result, error text ('' when it worked)

    def __init__(self, work: Callable):
        super().__init__()
        self.work = work

    def run(self):
        try:
            result = self.work(self.said.emit)
            self.done.emit(result, "")
        except Exception as exc:                          # noqa: BLE001 - shown to the person
            self.done.emit(None, str(exc) or exc.__class__.__name__)


class RecoveryKeyDialog(QDialog):
    """Shows a new Recovery Key once, with what to do with it."""

    def __init__(self, key: str, parent=None, first_time: bool = True):
        super().__init__(parent)
        self.key = key
        self.setWindowTitle("Slate Recovery Key")
        self.setMinimumWidth(620)
        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        title = QLabel("Your Slate Recovery Key" if first_time else "Your new Recovery Key")
        title.setStyleSheet(f"font-size: 18px; font-weight: {T.WEIGHT_BOLD};")
        layout.addWidget(title)
        shown = QLineEdit(key)
        shown.setReadOnly(True)
        shown.setAlignment(Qt.AlignmentFlag.AlignCenter)
        shown.setStyleSheet("font-family: Consolas, monospace; font-size: 22px; padding: 10px;")
        self.shown = shown
        layout.addWidget(shown)
        layout.addWidget(_note(
            "Print this or write it down now, and keep it somewhere safe that is not this "
            "PC - a locked drawer, the studio safe or a password manager.\n\n"
            "With this key, on this server PC, someone can get back into Slate when nobody "
            "can sign in: reset a password, restore an administrator, or undo a security "
            "change. Slate keeps only a one-way fingerprint of it, so this is the only time "
            "it is shown.\n\n"
            "If it is ever lost: run Recover Slate as a Windows administrator on this PC and "
            "make a new one. The old key stops working at once."
            + ("" if first_time else "\n\nThe previous key no longer works.")))
        buttons = QHBoxLayout()
        copy = QPushButton("Copy")
        copy.clicked.connect(lambda: QApplication.clipboard().setText(key))
        save = QPushButton("Save as a text file...")
        save.clicked.connect(self._save)
        printing = QPushButton("Print...")
        printing.clicked.connect(self._print)
        for button in (copy, save, printing):
            buttons.addWidget(button)
        buttons.addStretch()
        layout.addLayout(buttons)
        self.stored = QCheckBox("I have printed or stored the key somewhere safe")
        layout.addWidget(self.stored)
        self.close_button = QPushButton("Close")
        self.close_button.setEnabled(False)
        self.close_button.clicked.connect(self.accept)
        self.stored.toggled.connect(self.close_button.setEnabled)
        layout.addWidget(self.close_button, 0, Qt.AlignmentFlag.AlignRight)

    def _text(self) -> str:
        return ("SLATE RECOVERY KEY\n\n    %s\n\nKeep this somewhere safe that is not the "
                "server PC.\nUse it with Recover Slate on the server PC when nobody can sign "
                "in.\nSee docs/RECOVERY.md.\n" % self.key)

    def _save(self):
        path, _ = QFileDialog.getSaveFileName(self, "Save the Recovery Key",
                                              "Slate Recovery Key.txt", "Text (*.txt)")
        if path:
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(self._text())

    def _print(self):
        try:
            from PySide6.QtGui import QTextDocument
            from PySide6.QtPrintSupport import QPrintDialog, QPrinter
        except Exception as exc:                           # pragma: no cover
            QMessageBox.warning(self, "Printing", "Printing is not available here: %s" % exc)
            return
        printer = QPrinter()
        if QPrintDialog(printer, self).exec() == QDialog.DialogCode.Accepted:
            document = QTextDocument()
            document.setPlainText(self._text())
            document.print_(printer)

    def reject(self):
        # Escape and the title-bar X still work - but only once it is stored.
        if self.stored.isChecked():
            super().reject()


class RecoveryView(QWidget):
    """The Recovery page. ``layout_provider`` returns the ServerLayout to act on."""

    def __init__(self, layout_provider: Callable, parent=None):
        super().__init__(parent)
        self.layout_provider = layout_provider
        self.session = None
        self._threads = []
        self.setup_ui()

    # ------------------------------------------------------------------ setup
    def setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(40, 40, 40, 40)
        root.setSpacing(18)
        title = QLabel("Recovery")
        title.setStyleSheet(f"font-size: 28px; font-weight: {T.WEIGHT_BOLD}; "
                            f"color: {C.TEXT_PRIMARY};")
        root.addWidget(title)
        root.addWidget(_note(
            "For when nobody can sign in, or a security change went wrong. It works only on "
            "this server PC, and only with the Recovery Key. It never needs a Slate or "
            "database password. Every change keeps a snapshot first."))

        # Health
        panel = _panel()
        box = QVBoxLayout(panel)
        box.setContentsMargins(24, 20, 24, 20)
        box.addWidget(_heading("Health check", "What is wrong, in plain words. Changes nothing."))
        row = QHBoxLayout()
        self.btn_health = _button("Check now", "primary")
        self.btn_health.clicked.connect(self.run_health)
        self.btn_stop_pool = _button("Stop leftover pool")
        self.btn_stop_pool.clicked.connect(self._stop_pool)
        self.btn_close_window = _button("Close an interrupted repair")
        self.btn_close_window.clicked.connect(self._close_window)
        for b in (self.btn_health, self.btn_stop_pool, self.btn_close_window):
            row.addWidget(b)
        row.addStretch()
        box.addLayout(row)
        self.health_text = QPlainTextEdit()
        self.health_text.setReadOnly(True)
        self.health_text.setMinimumHeight(180)
        box.addWidget(self.health_text)
        root.addWidget(panel)

        # Unlock
        panel = _panel()
        box = QVBoxLayout(panel)
        box.setContentsMargins(24, 20, 24, 20)
        box.addWidget(_heading("Unlock", "Type the Recovery Key that was printed when this "
                                         "server was set up. Case, spaces and dashes do not matter."))
        row = QHBoxLayout()
        self.key_field = _field("XXXXX-XXXXX-XXXXX-XXXXX-XXXXX", secret=True)
        row.addWidget(self.key_field, 1)
        self.btn_unlock = _button("Unlock", "primary")
        self.btn_unlock.clicked.connect(self.unlock)
        row.addWidget(self.btn_unlock)
        box.addLayout(row)
        self.lbl_lock = _note("Locked.")
        box.addWidget(self.lbl_lock)
        root.addWidget(panel)

        # Accounts
        panel = _panel()
        box = QVBoxLayout(panel)
        box.setContentsMargins(24, 20, 24, 20)
        box.addWidget(_heading("Accounts", "A new password is for this one sign-in: the person "
                                           "chooses their own straight after."))
        self.user_field = _field("Username, e.g. admin")
        self.user_pw = _field("New password (8 or more characters)", secret=True)
        self.user_pw2 = _field("Type it again", secret=True)
        for w in (self.user_field, self.user_pw, self.user_pw2):
            box.addWidget(w)
        row = QHBoxLayout()
        self.btn_reset = _button("Reset password and switch the account back on", "primary")
        self.btn_reset.clicked.connect(self._reset_password)
        self.btn_admin = _button("Create or restore administrator")
        self.btn_admin.clicked.connect(self._restore_admin)
        row.addWidget(self.btn_reset)
        row.addWidget(self.btn_admin)
        row.addStretch()
        box.addLayout(row)
        root.addWidget(panel)

        # Passwords
        panel = _panel()
        box = QVBoxLayout(panel)
        box.setContentsMargins(24, 20, 24, 20)
        box.addWidget(_heading(
            "Database passwords",
            "Two separate passwords. The app password is the one every workstation has; "
            "changing it means changing it on every workstation too (you will be told how). "
            "If workstations were locked out because it changed on the server, set it back to "
            "the one they have. The superuser password is used only by this server."))
        self.db_pw = _field("New password (8 or more characters)", secret=True)
        self.db_pw2 = _field("Type it again", secret=True)
        box.addWidget(self.db_pw)
        box.addWidget(self.db_pw2)
        row = QHBoxLayout()
        self.btn_app_pw = _button("Set app password (workstations)")
        self.btn_app_pw.clicked.connect(self._set_app_password)
        self.btn_super_pw = _button("Set superuser password (this server)")
        self.btn_super_pw.clicked.connect(self._set_superuser_password)
        row.addWidget(self.btn_app_pw)
        row.addWidget(self.btn_super_pw)
        row.addStretch()
        box.addLayout(row)
        root.addWidget(panel)

        # Switches + snapshots + key
        panel = _panel()
        box = QVBoxLayout(panel)
        box.setContentsMargins(24, 20, 24, 20)
        box.addWidget(_heading("Security switches and snapshots",
                               "Turning a switch off puts back the old behaviour, even with "
                               "the database down. Log only changes nothing but writes down "
                               "what the switch would refuse; Turn on checks first that "
                               "people can still get in, and undoes itself if not. A "
                               "snapshot is taken before every security change and before "
                               "every repair here."))
        row = QHBoxLayout()
        self.switch_pick = QComboBox()
        self.switch_pick.addItem("Every security switch", None)
        try:
            from slate.core.security import switches
            for name in sorted(switches.known()):
                self.switch_pick.addItem(name, name)
        except Exception:
            pass
        row.addWidget(self.switch_pick, 1)
        self.btn_switch_off = _button("Turn off")
        self.btn_switch_off.clicked.connect(self._switches_off)
        self.btn_switch_log = _button("Log only")
        self.btn_switch_log.clicked.connect(lambda: self._switch_on("log_only"))
        self.btn_switch_on = _button("Turn on")
        self.btn_switch_on.clicked.connect(lambda: self._switch_on("on"))
        for button in (self.btn_switch_off, self.btn_switch_log, self.btn_switch_on):
            row.addWidget(button)
        box.addLayout(row)
        self._modes = None
        self.switch_about = _note("")
        box.addWidget(self.switch_about)
        self.switch_pick.currentIndexChanged.connect(self._show_switch)
        self._show_switch()
        row = QHBoxLayout()
        self.chk_accounts = QCheckBox("Also put the users and roles back")
        self.chk_accounts.setStyleSheet(f"color: {C.TEXT_PRIMARY}; background: transparent; "
                                        "border: none;")
        self.btn_restore = _button("Restore the last snapshot", "danger")
        self.btn_restore.clicked.connect(self._restore_snapshot)
        row.addWidget(self.btn_restore)
        row.addWidget(self.chk_accounts)
        row.addStretch()
        box.addLayout(row)
        row = QHBoxLayout()
        self.btn_new_key = _button("Make a new Recovery Key")
        self.btn_new_key.clicked.connect(self._new_key)
        row.addWidget(self.btn_new_key)
        row.addWidget(_note("Uses the key typed above, or - if it is lost - needs this "
                            "window run as a Windows administrator."), 1)
        box.addLayout(row)
        root.addWidget(panel)

        # Output
        self.output = QPlainTextEdit()
        self.output.setStyleSheet(
            f"background: {C.BG_SURFACE}; color: {C.TEXT_PRIMARY}; border: 1px solid "
            f"{C.BORDER_DEFAULT}; border-radius: 8px; padding: 6px;")
        self.output.setReadOnly(True)
        self.output.setMinimumHeight(140)
        self.output.setPlaceholderText("What the recovery tool did appears here.")
        root.addWidget(self.output)

    # --------------------------------------------------------------- plumbing
    def _layout(self):
        return self.layout_provider()

    def _session(self):
        from slate_server.core.recovery.session import RecoverySession
        layout = self._layout()
        if self.session is None or self.session.layout.data_dir != layout.data_dir:
            self.session = RecoverySession(layout)
        return self.session

    def say(self, text: str):
        self.output.appendPlainText(text)

    def _busy(self, busy: bool):
        for button in self.findChildren(QPushButton):
            button.setEnabled(not busy)

    def run_job(self, work: Callable, on_done: Optional[Callable] = None,
                wait: bool = False):
        """work(say) runs on a thread; on_done(result) back on the screen's."""
        job = _Job(work)
        thread = QThread(self)
        job.moveToThread(thread)
        thread.started.connect(job.run)
        job.said.connect(self.say)

        def finished(result, error):
            self._busy(False)
            if error:
                self.say("Not done: " + error)
            elif on_done is not None:
                on_done(result)
            thread.quit()

        job.done.connect(finished)
        thread.finished.connect(lambda: self._threads.remove((thread, job))
                                if (thread, job) in self._threads else None)
        self._threads.append((thread, job))
        self._busy(True)
        thread.start()
        if wait:
            thread.wait(120000)
        return thread

    def _matching(self, first: QLineEdit, second: QLineEdit) -> Optional[str]:
        a, b = first.text(), second.text()
        if a != b:
            QMessageBox.warning(self, "Recovery", "The two passwords are not the same.")
            return None
        if len(a.strip()) < 8:
            QMessageBox.warning(self, "Recovery", "Use at least 8 characters.")
            return None
        return a

    def _clear(self, *fields):
        for field in fields:
            field.clear()

    # ---------------------------------------------------------------- actions
    def run_health(self):
        from slate_server.core.recovery import health
        layout = self._layout()
        self.health_text.setPlainText("Checking...")

        def work(say):
            return health.report(health.health_check(layout))

        self.run_job(work, self.health_text.setPlainText)

    def _stop_pool(self):
        from slate_server.core.recovery import health
        layout = self._layout()
        self.run_job(lambda say: health.stop_leftover_pool(layout), self.say)

    def _close_window(self):
        session = self._session()
        self.run_job(lambda say: session.close_leftover_window(),
                     lambda closed: self.say("Closed an interrupted repair's way in." if closed
                                             else "No way in was left open."))

    def unlock(self):
        session = self._session()
        session.say = None
        key = self.key_field.text()
        self.key_field.clear()

        def done(_):
            self.lbl_lock.setText("Unlocked for ten minutes.")
            self.say("Unlocked.")
            self.refresh_switches()

        def work(say):
            session.say = say
            session.unlock(key)
            return True

        self.run_job(work, done)

    def _unlocked_session(self):
        session = self._session()
        if not session.unlocked:
            QMessageBox.information(self, "Recovery", "Unlock with the Recovery Key first.")
            return None
        return session

    def _confirm(self, text: str) -> bool:
        return QMessageBox.question(
            self, "Recovery", text,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel) == QMessageBox.StandardButton.Yes

    def _in_session(self, work, on_done=None):
        session = self._unlocked_session()
        if session is None:
            return

        def run(say):
            session.say = say
            return work(session)

        self.run_job(run, on_done)

    def _reset_password(self):
        user = self.user_field.text().strip()
        password = self._matching(self.user_pw, self.user_pw2)
        if not user or password is None:
            return
        self._clear(self.user_pw, self.user_pw2)
        self._in_session(lambda s: s.reset_password(user, password))

    def _restore_admin(self):
        user = self.user_field.text().strip() or "admin"
        password = self._matching(self.user_pw, self.user_pw2)
        if password is None:
            return
        self._clear(self.user_pw, self.user_pw2)
        self._in_session(lambda s: s.restore_admin(user, password))

    def _set_app_password(self):
        password = self._matching(self.db_pw, self.db_pw2)
        if password is None or not self._confirm(
                "Every workstation must have this same password, or it cannot open Slate. "
                "Set it?"):
            return
        self._clear(self.db_pw, self.db_pw2)
        self._in_session(lambda s: s.set_app_password(password))

    def _set_superuser_password(self):
        password = self._matching(self.db_pw, self.db_pw2)
        if password is None:
            return
        self._clear(self.db_pw, self.db_pw2)
        self._in_session(lambda s: s.set_superuser_password(password))

    def _switches_off(self):
        name = self.switch_pick.currentData()
        self._in_session(lambda s: s.switches_off(None if name is None else [name]),
                         lambda _: self.refresh_switches())

    def _show_switch(self, *_):
        name = self.switch_pick.currentData()
        if name is None:
            self.switch_about.setText("Every security switch: Turn off only. Switches are "
                                      "turned on one at a time.")
            return
        from slate.core.security import switches
        mode = (self._modes or {}).get(name) or "not known (unlock, with the database running, to see it)"
        text = "%s - now: %s. %s" % (name, mode, switches.known().get(name, ""))
        if name.startswith("signed_"):
            text += " Recommended: Log only first; turn it on once the log shows nothing " \
                    "would be refused."
        self.switch_about.setText(text)

    def refresh_switches(self):
        """Read every switch's mode off the screen's thread, then show the chosen one."""
        from slate_server.core.recovery.hardening import current_modes
        layout = self._layout()

        def done(modes):
            self._modes = modes
            if modes is None:
                self.say("The switches' modes cannot be read: the database cannot be reached.")
            self._show_switch()

        self.run_job(lambda say: current_modes(layout), done)

    def _switch_on(self, mode: str):
        name = self.switch_pick.currentData()
        if name is None:
            QMessageBox.information(self, "Recovery", "Choose one switch: they are turned on "
                                                      "one at a time.")
            return
        if self._unlocked_session() is None:
            return
        if mode == "on" and name.startswith("signed_") and \
                (self._modes or {}).get(name) != "log_only":
            question = ("%s should run as Log only first, so a mistake shows up as log lines "
                        "rather than as nobody being able to work. Turn it on anyway?" % name)
        elif mode == "on":
            question = ("Turn %s on? It is checked first and undone on its own if anybody "
                        "would be locked out." % name)
        else:
            question = ("Set %s to Log only? Nothing is refused; what it would refuse is "
                        "written to the log." % name)
        if not self._confirm(question):
            return
        self._in_session(lambda s: s.turn_on(name, mode), lambda _: self.refresh_switches())

    def _restore_snapshot(self):
        accounts = self.chk_accounts.isChecked()
        if not self._confirm("Put back the files from the last snapshot%s? The current ones "
                             "are kept as a snapshot first." %
                             (", and the users and roles" if accounts else "")):
            return
        self._in_session(lambda s: s.restore_latest_snapshot(accounts=accounts))

    def _new_key(self):
        session = self._session()
        old = self.key_field.text().strip() or None
        self.key_field.clear()

        def work(say):
            return session.new_key(old_key=old, as_admin=old is None)

        self.run_job(work, lambda made: RecoveryKeyDialog(made, self, first_time=False).exec())
