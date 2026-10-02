"""
Deployment: a log of software installed on workstations.

It does not install anything. It never did - the tab was called Deployment
and its button said "Deploy New Package", which reads as a promise that
something is pushed to the machine named in the row. Nothing is: this is a
log somebody fills in, and calling it one is the difference between a useful
record and a feature that appears broken.

If it is ever made real, the channel already exists: ServerHub.post_command
is how the admin panel tells workstations to clear their caches, and a
deployment is the same shape of instruction. (Remote commands are on the
security list; nothing here sends one.)
"""

import logging

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox, QCompleter, QDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
    QPlainTextEdit, QTableWidget, QVBoxLayout, QWidget,
)

from ..core.empty_state import EmptyState
from ..core.controls import enable_with_selection, make_button, page_title, tidy_form
from ..core.stat_card import StatStrip
from ..core.table_style import dim_cell, set_cell_status, style_table
from slate.gui.core.offline_notice import on_database_error
from slate.core.infra.gate import Gate
from slate.gui.core.data_display import datetime_item, export_table_dialog
from slate.core.domain import people
from slate.core.infra.deployment_repository import (
    STATUSES, DeploymentError, DeploymentRepository, split_machines, success_rate,
)
from slate.gui.components import feedback
from slate.gui.components.table_tools import (
    KeepSelection, TableToolbar, make_item, select_keys, selected_keys, setup_table,
)
from slate.gui.components.state_notice import clear_state, show_load_error

logger = logging.getLogger(__name__)

# Let an outage reach the @on_database_error decorator rather than becoming an
# empty grid here. Everything else keeps the fallback it already had.
try:
    from slate.core.infra.postgres_manager import DatabaseUnavailableError
except ImportError:                                  # pragma: no cover
    class DatabaseUnavailableError(ConnectionError):
        """Fallback when the manager cannot be imported."""

COLUMNS = ["ID", "Package", "Version", "Machine", "Recorded by", "Status", "Recorded at",
           "Outcome by", "Notes"]
COL = {name: i for i, name in enumerate(COLUMNS)}
TONE = {"Success": "ok", "Failed": "bad", "Pending": "warn"}


class AddDeploymentDialog(QDialog):
    """Record an install (several machines at once), or correct a record."""

    def __init__(self, parent=None, machines=(), record=None):
        super().__init__(parent)
        self.record = record or {}
        self.known = {m.lower(): m for m in machines}
        self.setWindowTitle("Edit record" if record else "Record a deployment")
        self.setMinimumWidth(480)
        root = QVBoxLayout(self)
        root.setContentsMargins(Gate.SPACE_4, Gate.SPACE_4, Gate.SPACE_4, Gate.SPACE_4)
        root.setSpacing(Gate.SPACE_3)
        layout = tidy_form(QFormLayout())

        self.pkg_input = QLineEdit(str(self.record.get("package_name") or ""))
        self.pkg_input.setPlaceholderText("e.g. Nuke 15.1, OCIO config, NVIDIA driver")
        self.version_input = QLineEdit(str(self.record.get("version") or ""))
        self.version_input.setPlaceholderText("e.g. 15.1v3")
        # Machines from the inventory, with completion; several separated by
        # commas when the same thing went on more than one.
        self.target_input = QLineEdit(str(self.record.get("target_machine") or ""))
        self.target_input.setPlaceholderText("WS-COMP-01, WS-COMP-02" if not record else "")
        completer = QCompleter(sorted(self.known.values(), key=str.lower), self)
        completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        completer.setFilterMode(Qt.MatchFlag.MatchContains)
        self.target_input.setCompleter(completer)
        self.notes_input = QPlainTextEdit(str(self.record.get("notes") or ""))
        self.notes_input.setFixedHeight(64)
        self.notes_input.setPlaceholderText("Anything worth knowing - why it failed, what was changed")

        # Labels without colons, like the other IT dialogs.
        layout.addRow("Package", self.pkg_input)
        layout.addRow("Version", self.version_input)
        layout.addRow("Machine" if record else "Machines", self.target_input)
        layout.addRow("Notes", self.notes_input)
        root.addLayout(layout)

        self.hint = QLabel("")
        self.hint.setWordWrap(True)
        self.hint.setStyleSheet(f"color: {Gate.TEXT_DIM}; font-size: {Gate.SIZE_SM}px;")
        root.addWidget(self.hint)

        # Enter records the deployment; Cancel is never the default.
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        self.cancel_btn = make_button("Cancel", on_click=self.reject)
        self.save_btn = make_button("Save" if record else "Record", "primary", on_click=self._save)
        btn_layout.addWidget(self.cancel_btn)
        btn_layout.addWidget(self.save_btn)
        root.addLayout(btn_layout)
        self.target_input.textChanged.connect(self._show_unknown)
        self._show_unknown()

    def machines(self):
        return split_machines(self.target_input.text())

    def unknown(self):
        return [m for m in self.machines() if m.lower() not in self.known]

    def _show_unknown(self, *_):
        unknown = self.unknown()
        self.hint.setText("Not in Hardware: %s" % ", ".join(unknown) if unknown else
                          ("%d machines - one record each." % len(self.machines())
                           if len(self.machines()) > 1 else ""))

    def _save(self):
        if not self.pkg_input.text().strip() or not self.machines():
            feedback.warn(self, self.windowTitle(), "Give both the package and the machine.")
            return
        if self.record and len(self.machines()) > 1:
            feedback.warn(self, self.windowTitle(), "A record is for one machine.")
            return
        unknown = self.unknown()
        if unknown and not feedback.confirm(
                self, self.windowTitle(),
                "%s %s not in the Hardware inventory. Record anyway?"
                % (", ".join(unknown), "is" if len(unknown) == 1 else "are"),
                yes_label="Record anyway", no_label="Check the name"):
            self.target_input.setFocus()
            return
        # The inventory's spelling of a known name.
        self.target_input.setText(", ".join(self.known.get(m.lower(), m) for m in self.machines()))
        self.accept()


class ItDeploymentTab(QWidget):
    """A record of software installed on workstations (see the module notes)."""

    def __init__(self, user_data=None, parent=None):
        super().__init__(parent)
        self.user_data = user_data or {}
        self.repo = DeploymentRepository()
        self._rows = []
        main_layout = QVBoxLayout(self)
        # The same margins as the other IT screens.
        main_layout.setContentsMargins(Gate.SPACE_5, Gate.SPACE_4, Gate.SPACE_5, Gate.SPACE_4)
        main_layout.setSpacing(Gate.SPACE_3)
        self.build_ui(main_layout)

    def build_ui(self, main_layout):
        main_layout.addWidget(page_title(
            'Deployment log',
            'A record of what was installed where. Slate does not push it.'))

        # Figures. Worded for a log, not a deploy system: "Pending" meant
        # "somebody typed it and has not said how it went".
        strip = StatStrip(compact=True)
        self.lbl_total = strip.add("Installs recorded", "0", tone="neutral")
        self.lbl_success = strip.add("Success rate", "-", tone="ok",
                                     tooltip="Of the installs with an outcome")
        self.lbl_pending = strip.add("Awaiting outcome", "0", tone="warn",
                                     on_click=lambda: self._filter_status("Pending"),
                                     tooltip="Show the installs nobody has marked yet")
        main_layout.addWidget(strip)

        # Actions on the right, like the other IT tabs; filters sit in the
        # search row below.
        controls = QHBoxLayout()
        controls.setSpacing(Gate.SPACE_2)
        controls.addStretch()
        self.btn_record = make_button("Record deployment", "primary", icon="plus",
                                      on_click=self.add_deployment)
        self.btn_success = make_button("Mark success", on_click=lambda: self.update_status("Success"))
        self.btn_failed = make_button("Mark failed", "danger", on_click=lambda: self.update_status("Failed"))
        self.btn_edit = make_button("Edit", on_click=self.edit_deployment)
        self.btn_delete = make_button("Delete", "danger", on_click=self.delete_deployment)
        for b in (self.btn_record, self.btn_success, self.btn_failed, self.btn_edit, self.btn_delete):
            controls.addWidget(b)
        controls.addWidget(make_button("Export\u2026", "ghost", tooltip="Save the rows shown as CSV or Excel",
                                       on_click=self.export_table))
        main_layout.addLayout(controls)

        self.grid = QTableWidget(0, len(COLUMNS))
        self.grid.setHorizontalHeaderLabels(COLUMNS)
        self.style_table(self.grid)
        # Read-only (typed cells were never saved), rows, sortable headers;
        # double-click edits.
        setup_table(self.grid)
        self.grid.doubleClicked.connect(lambda _i: self.edit_deployment())
        self.toolbar = TableToolbar(self.grid, placeholder="Search package, machine, person or notes\u2026",
                                    columns=(1, 2, 3, 4, 7, 8), on_refresh=self.load_data)
        self.filter_cb = self.toolbar.add_filter(
            "Status", [("All statuses", "")] + [(s, s) for s in STATUSES], column=COL["Status"])
        main_layout.addWidget(self.toolbar)
        # Other people's records appear without a restart: the change feed,
        # or a timer where it is missing.
        from slate.gui.components.auto_refresh import AutoRefresh
        self._auto_refresh = AutoRefresh(self, self.load_data, seconds=30,
                                         topics=("it_deployments",))

        self.grid.hideColumn(COL["ID"])
        main_layout.addWidget(self.grid)

        self.empty_state = EmptyState(
            'Nothing recorded yet',
            'Add an install with Record deployment.',
            glyph='package',
        )
        main_layout.addWidget(self.empty_state)
        self.empty_state.attach_to(self.grid)

        for button in (self.btn_success, self.btn_failed, self.btn_edit, self.btn_delete):
            enable_with_selection(button, self.grid)

        # First read only now that the table is in the layout: a notice for a
        # failed read takes the table's place.
        self.load_data()

    def _filter_status(self, status):
        self.filter_cb.setCurrentIndex(max(0, self.filter_cb.findData(status)))

    def _user(self) -> str:
        # Never the literal 'admin' when the signed-in name is missing.
        data = self.user_data or {}
        return str(data.get("user_id") or data.get("username") or "").strip() or "unknown"

    @on_database_error
    def load_data(self, *_):
        try:
            rows = self.repo.all()
        except DatabaseUnavailableError:
            raise
        except Exception as e:
            # Not "Nothing deployed yet": the read failed, so say that.
            logging.exception("Deployments could not be read")
            show_load_error(self, e, retry=self.load_data, what="the deployment log")
            return
        clear_state(self)
        self._rows = rows

        pending = sum(1 for d in rows if d.get('status') == 'Pending')
        self.lbl_total.set_value(len(rows))
        self.lbl_pending.set_value(pending)
        self.lbl_pending.set_tone("warn" if pending else "idle")
        # Out of the ones that finished, rounded: 2 of 3 is 67%, not 66%.
        rate = success_rate(rows)
        self.lbl_success.set_value("-" if rate is None else f"{rate}%")

        # The selection follows the deployment (by id), not the row number.
        with KeepSelection(self.grid):
            self._fill(rows)

    def _fill(self, deps):
        self.grid.setRowCount(len(deps))
        for r, row in enumerate(deps):
            dep_id = row.get('id')
            self.grid.setItem(r, COL["ID"], make_item(str(dep_id or ''), sort_value=dep_id, key=dep_id))
            self.grid.setItem(r, COL["Package"], make_item(str(row.get('package_name') or '')))
            version = make_item(str(row.get('version') or '\u2014'))
            if not row.get('version'):
                dim_cell(version)
            self.grid.setItem(r, COL["Version"], version)
            self.grid.setItem(r, COL["Machine"], make_item(str(row.get('target_machine') or '')))
            # The person's name, not their login.
            self.grid.setItem(r, COL["Recorded by"], make_item(people.display_name(row.get('deployed_by', ''))))

            status_item = make_item(row.get('status') or '')
            tone = TONE.get(row.get('status'))
            if tone:
                set_cell_status(status_item, tone, background=False)
            else:
                dim_cell(status_item)
            self.grid.setItem(r, COL["Status"], status_item)
            # '17 Sep 2026, 23:02', sorted by the moment, not the text.
            self.grid.setItem(r, COL["Recorded at"], datetime_item(row.get('deployed_at')))
            done_by = row.get("completed_by")
            outcome = make_item(people.display_name(done_by) if done_by else '\u2014')
            if done_by and row.get("completed_at"):
                from slate.core.domain.dates import format_datetime
                outcome.setToolTip("Marked %s on %s" % (row.get("status", "").lower(),
                                                        format_datetime(row.get("completed_at"))))
            else:
                dim_cell(outcome)
            self.grid.setItem(r, COL["Outcome by"], outcome)
            notes = str(row.get("notes") or "")
            note_item = make_item(notes.splitlines()[0] if notes else "", tooltip=notes)
            self.grid.setItem(r, COL["Notes"], note_item)

    def _selected(self):
        ids = [k for k in selected_keys(self.grid) if k is not None]
        by_id = {d.get("id"): d for d in self._rows}
        return [by_id[i] for i in ids if i in by_id]

    # ------------------------------------------------------------ actions
    def add_deployment(self):
        try:
            machines = self.repo.known_machines()
        except DatabaseUnavailableError:
            raise
        except Exception:
            logger.exception("Machine names not read for the completer")
            machines = []
        dialog = AddDeploymentDialog(self, machines)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        pkg = dialog.pkg_input.text().strip()
        targets = dialog.machines()
        try:
            ids = self.repo.record(pkg, targets, self._user(), dialog.version_input.text(),
                                   dialog.notes_input.toPlainText())
        except DatabaseUnavailableError:
            raise
        except Exception as exc:
            feedback.warn(self, "Record deployment", "Nothing was recorded:\n\n%s" % exc)
            self.load_data()
            return
        self.load_data()
        select_keys(self.grid, [i for i in ids if i is not None])
        feedback.toast(self, "Recorded: %s on %s." % (pkg, ", ".join(targets)), "success")

    def edit_deployment(self):
        picked = self._selected()
        if len(picked) != 1:
            return
        record = picked[0]
        try:
            machines = self.repo.known_machines()
        except DatabaseUnavailableError:
            raise
        except Exception:
            machines = []
        dialog = AddDeploymentDialog(self, machines, record=record)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            self.repo.update(record["id"], dialog.pkg_input.text(), dialog.machines()[0],
                             dialog.version_input.text(), dialog.notes_input.toPlainText())
        except DatabaseUnavailableError:
            raise
        except Exception as exc:
            feedback.warn(self, "Edit record", "The change was not saved:\n\n%s" % exc)
        else:
            feedback.toast(self, "Saved.", "success")
        self.load_data()

    def delete_deployment(self):
        picked = self._selected()
        if not picked:
            return
        what = ("%s on %s" % (picked[0].get("package_name"), picked[0].get("target_machine"))
                if len(picked) == 1 else "%d records" % len(picked))
        if not feedback.confirm(self, "Delete record", "Delete %s from the log? This cannot be undone." % what,
                                yes_label="Delete", no_label="Keep", destructive=True):
            return
        failed = []
        for record in picked:
            try:
                self.repo.delete(record["id"])
            except DatabaseUnavailableError:
                raise
            except Exception as exc:
                failed.append(str(exc))
        self.load_data()
        if failed:
            feedback.warn(self, "Delete record", "Not everything was deleted:\n\n%s" % failed[0])
        else:
            feedback.toast(self, "Deleted %s." % what, "success")

    def update_status(self, new_status):
        # By id, never by row number: after a reload the same rows hold other
        # deployments, and the next Mark Failed used to hit one of those.
        picked = self._selected()
        if not picked:
            feedback.warn(self, "Mark %s" % new_status.lower(), "Select the deployment to update.")
            return
        # Changing an outcome somebody already recorded is asked, not silent.
        finished = [d for d in picked if d.get("status") in ("Success", "Failed")
                    and d.get("status") != new_status]
        if finished and not feedback.confirm(
                self, "Mark %s" % new_status.lower(),
                "%d of these already %s an outcome (%s). Change %s to %s?"
                % (len(finished), "has" if len(finished) == 1 else "have",
                   ", ".join(sorted({d["status"] for d in finished})),
                   "it" if len(finished) == 1 else "them", new_status),
                yes_label="Change to %s" % new_status, no_label="Keep"):
            return
        note = self._failure_reason() if new_status == "Failed" else None
        failed = []
        for record in picked:
            try:
                self.repo.set_outcome(record["id"], new_status, self._user(), note)
            except DatabaseUnavailableError:
                raise
            except Exception as exc:
                failed.append(str(exc))
        self.load_data()
        if failed:
            feedback.warn(self, "Mark %s" % new_status.lower(),
                          "%d of %d were not marked %s:\n\n%s"
                          % (len(failed), len(picked), new_status, failed[0]))
        else:
            feedback.toast(self, "Marked %s." % new_status.lower(), "success")

    def _failure_reason(self):
        """Why it failed, for the Notes column (optional)."""
        from PySide6.QtWidgets import QInputDialog
        dialog = QInputDialog(self)
        dialog.setWindowTitle("Mark failed")
        dialog.setLabelText("Why did it fail? (kept in Notes - optional)")
        if dialog.exec() == QDialog.DialogCode.Accepted and dialog.textValue().strip():
            return dialog.textValue().strip()
        return None

    def export_table(self):
        """What the table shows, to CSV or Excel (IT-159)."""
        export_table_dialog(self, self.grid, "deployments")

    def style_table(self, table: QTableWidget):
        """The shared table style (it was amber text, ALL-CAPS headers)."""
        style_table(table, {
            "Package": "stretch",
            "Version": "contents",
            "Machine": ("interactive", 150),
            "Recorded by": "contents",
            "Status": "contents",
            "Recorded at": "contents",
            "Outcome by": "contents",
            "Notes": ("interactive", 200),
        })
