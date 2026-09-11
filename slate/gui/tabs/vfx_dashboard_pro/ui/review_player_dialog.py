"""
Review player dialog.

Double-clicking a version in the Review Queue opens this dialog.
Embeds AdvancedPlayer for immediate media review with inline Approve/Retake
verdict buttons and feedback notes that write directly to VersionStore.
"""

from __future__ import annotations

import os
import logging
from typing import List, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTextEdit, QFrame, QMessageBox, QSplitter, QWidget, QScrollArea,
)

from slate.core.domain.versions import (
    Version, VersionStore, STATUS_APPROVED, STATUS_RETAKE, SENT_INTERNAL,
)
from slate.gui.widgets.advanced_player import AdvancedPlayer


class ReviewPlayerDialog(QDialog):
    """Integrated review player with Approve/Retake verdict actions and note taking."""

    verdict_submitted = Signal(object, str, str)  # (version, status, note_text)

    def __init__(self, version: Version, queue_versions: Optional[List[Version]] = None,
                 current_index: int = 0, store: Optional[VersionStore] = None,
                 current_user: str = "", parent=None):
        super().__init__(parent)
        self.version = version
        self.queue_versions = list(queue_versions or [version])
        self.current_index = current_index
        self.store = store or VersionStore()
        self.current_user = str(current_user or "supervisor").strip()

        self.setWindowTitle(f"Review: {version.shot_name} - {version.version_name}")
        self.resize(1280, 800)
        self.setModal(True)
        self.setStyleSheet("""
            QDialog {
                background-color: #0D0D0F;
                color: #E8E6E1;
            }
        """)

        self._setup_ui()
        self._load_version(self.version)

    def _setup_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(12, 12, 12, 12)
        main_layout.setSpacing(8)

        # 1. Header Information Bar
        self.header_bar = QFrame()
        self.header_bar.setObjectName("reviewHeaderBar")
        self.header_bar.setFixedHeight(46)
        self.header_bar.setStyleSheet("""
            QFrame#reviewHeaderBar {
                background: #16161A;
                border: 1px solid #1D1D22;
                border-radius: 6px;
            }
        """)
        header_layout = QHBoxLayout(self.header_bar)
        header_layout.setContentsMargins(12, 0, 12, 0)
        header_layout.setSpacing(10)

        self.title_label = QLabel()
        self.title_label.setStyleSheet("font-size: 13px; font-weight: 700; color: #E8E6E1; background: transparent; border: none;")
        header_layout.addWidget(self.title_label)

        self.meta_label = QLabel()
        self.meta_label.setStyleSheet("font-size: 11px; color: #87857F; background: transparent; border: none;")
        header_layout.addWidget(self.meta_label)

        header_layout.addStretch()

        # Queue Navigation
        nav_btn_style = """
            QPushButton {
                background: #1D1D22;
                border: 1px solid #26262D;
                border-radius: 4px;
                color: #E8E6E1;
                font-size: 11px;
                font-weight: 600;
                padding: 4px 10px;
            }
            QPushButton:hover {
                background: #26262D;
                border-color: #2C2C34;
                color: #3EA8BF;
            }
            QPushButton:disabled {
                background: #16161A;
                border-color: #1D1D22;
                color: #2C2C34;
            }
        """
        self.prev_btn = QPushButton("◀ Previous")
        self.prev_btn.setStyleSheet(nav_btn_style)
        self.prev_btn.clicked.connect(self._on_previous_clicked)
        header_layout.addWidget(self.prev_btn)

        self.queue_pos_label = QLabel("1 / 1")
        self.queue_pos_label.setStyleSheet("color: #E8E6E1; font-weight: 600; font-size: 11px; padding: 0 8px; background: transparent; border: none;")
        header_layout.addWidget(self.queue_pos_label)

        self.next_btn = QPushButton("Next ▶")
        self.next_btn.setStyleSheet(nav_btn_style)
        self.next_btn.clicked.connect(self._on_next_clicked)
        header_layout.addWidget(self.next_btn)

        main_layout.addWidget(self.header_bar, 0)

        # 2. Main Content: Splitter with Player and Notes sidebar
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setStyleSheet("""
            QSplitter::handle {
                background: #1D1D22;
                width: 2px;
            }
        """)

        # Left: Media Player
        player_container = QWidget()
        player_container.setStyleSheet("background-color: #0D0D0F;")
        player_layout = QVBoxLayout(player_container)
        player_layout.setContentsMargins(0, 0, 0, 0)
        player_layout.setSpacing(4)

        self.player = AdvancedPlayer(player_container)
        player_layout.addWidget(self.player, 1)

        self.media_path_label = QLabel()
        self.media_path_label.setStyleSheet("font-size: 11px; color: #87857F; padding: 2px 4px; background: transparent;")
        player_layout.addWidget(self.media_path_label, 0)

        splitter.addWidget(player_container)

        # Right: Notes & History Sidebar
        sidebar = QFrame()
        sidebar.setObjectName("reviewSidebar")
        sidebar.setStyleSheet("""
            QFrame#reviewSidebar {
                background: #16161A;
                border: 1px solid #1D1D22;
                border-radius: 6px;
            }
        """)
        sidebar_layout = QVBoxLayout(sidebar)
        sidebar_layout.setContentsMargins(12, 12, 12, 12)
        sidebar_layout.setSpacing(8)

        sidebar_title = QLabel("VERSION HISTORY & NOTES")
        sidebar_title.setStyleSheet("font-size: 10px; font-weight: 700; color: #87857F; letter-spacing: 0.5px;")
        sidebar_layout.addWidget(sidebar_title)

        self.notes_scroll = QScrollArea()
        self.notes_scroll.setWidgetResizable(True)
        self.notes_scroll.setStyleSheet("border: none; background: transparent;")
        self.notes_content = QWidget()
        self.notes_content.setStyleSheet("background: transparent;")
        self.notes_list_layout = QVBoxLayout(self.notes_content)
        self.notes_list_layout.setContentsMargins(0, 0, 0, 0)
        self.notes_list_layout.setSpacing(6)
        self.notes_list_layout.addStretch()
        self.notes_scroll.setWidget(self.notes_content)
        sidebar_layout.addWidget(self.notes_scroll)

        # New Note Input
        new_note_label = QLabel("ADD REVIEW NOTE")
        new_note_label.setStyleSheet("font-size: 10px; font-weight: 700; color: #87857F; letter-spacing: 0.5px;")
        sidebar_layout.addWidget(new_note_label)

        self.note_edit = QTextEdit()
        self.note_edit.setPlaceholderText("Enter feedback or revision notes...")
        self.note_edit.setMaximumHeight(90)
        self.note_edit.setStyleSheet("""
            QTextEdit {
                background: #16161A;
                border: 1px solid #26262D;
                border-radius: 4px;
                color: #E8E6E1;
                padding: 8px;
                font-size: 12px;
            }
            QTextEdit:focus {
                border-color: #3EA8BF;
            }
        """)
        sidebar_layout.addWidget(self.note_edit)

        # Verdict Action Buttons
        verdict_layout = QHBoxLayout()
        verdict_layout.setSpacing(8)

        self.approve_btn = QPushButton("Approve")
        self.approve_btn.setStyleSheet("""
            QPushButton {
                background: #5FBF8F;
                color: #0D0D0F;
                font-weight: 700;
                font-size: 12px;
                padding: 8px 16px;
                border-radius: 4px;
                border: none;
            }
            QPushButton:hover { background: #5FBF8F; }
            QPushButton:pressed { background: #5FBF8F; }
        """)
        self.approve_btn.clicked.connect(self._on_approve_clicked)
        verdict_layout.addWidget(self.approve_btn)

        self.retake_btn = QPushButton("Retake")
        self.retake_btn.setStyleSheet("""
            QPushButton {
                background: #D9635F;
                color: #E8E6E1;
                font-weight: 700;
                font-size: 12px;
                padding: 8px 16px;
                border-radius: 4px;
                border: none;
            }
            QPushButton:hover { background: #D9635F; }
            QPushButton:pressed { background: #D9635F; }
        """)
        self.retake_btn.clicked.connect(self._on_retake_clicked)
        verdict_layout.addWidget(self.retake_btn)

        sidebar_layout.addLayout(verdict_layout)
        splitter.addWidget(sidebar)

        splitter.setStretchFactor(0, 7)  # 70% Player
        splitter.setStretchFactor(1, 3)  # 30% Notes & Actions
        main_layout.addWidget(splitter, 1)

    def _load_version(self, version: Version):
        self.version = version
        self.title_label.setText(f"{version.shot_name} · {version.version_name}")
        self.meta_label.setText(
            f"Dept: {version.department or '-'} | Artist: {version.artist or '-'} | "
            f"Status: {version.status} | Sent: {version.sent_date or '-'}"
        )
        total = len(self.queue_versions)
        idx_display = (self.current_index + 1) if total > 0 else 1
        self.queue_pos_label.setText(f"{idx_display} / {total}")
        self.prev_btn.setEnabled(self.current_index > 0)
        self.next_btn.setEnabled(self.current_index < total - 1)

        # Media loading
        media_path = str(version.media_path or "").strip()
        if media_path and os.path.exists(media_path):
            self.media_path_label.setText(f"Media: {media_path}")
            try:
                self.player.load(media_path)
            except Exception as exc:
                logging.warning("Failed to load media %s: %s", media_path, exc)
                self.media_path_label.setText(f"Load error: {exc}")
        else:
            self.media_path_label.setText(
                f"Media path not found or empty: {media_path or '(none)'}"
            )

        # Refresh notes history
        self._refresh_notes_list()

    def _refresh_notes_list(self):
        # Clear existing note widgets
        # Everything but the trailing stretch. Unparent as well as delete:
        # deleteLater() alone leaves the old notes painting over the new ones
        # until the event loop gets round to them.
        while self.notes_list_layout.count() > 1:
            child = self.notes_list_layout.takeAt(0)
            old = child.widget()
            if old is not None:
                old.setParent(None)
                old.deleteLater()

        notes = []
        if self.version and self.version.id > 0:
            try:
                notes = self.store.notes_for_version(self.version.id)
            except Exception as exc:
                logging.debug("Error fetching notes: %s", exc)

        if not notes:
            empty_lbl = QLabel("No notes attached yet.")
            empty_lbl.setStyleSheet("color: #87857F; font-style: italic; font-size: 12px; padding: 6px;")
            self.notes_list_layout.insertWidget(0, empty_lbl)
            return

        for idx, note in enumerate(notes):
            card = QFrame()
            card.setStyleSheet("""
                QFrame {
                    background: #16161A;
                    border: 1px solid #1D1D22;
                    border-radius: 6px;
                    padding: 6px;
                }
            """)
            card_l = QVBoxLayout(card)
            card_l.setContentsMargins(8, 6, 8, 6)
            card_l.setSpacing(4)

            header = QLabel(f"<b>{note.author or 'Unknown'}</b> ({note.source or 'internal'}) · {note.note_date}")
            header.setStyleSheet("font-size: 11px; color: #3EA8BF; font-weight: 600;")
            card_l.addWidget(header)

            body = QLabel(note.text)
            body.setStyleSheet("font-size: 12px; color: #E8E6E1; line-height: 1.4;")
            body.setWordWrap(True)
            card_l.addWidget(body)

            self.notes_list_layout.insertWidget(idx, card)

    def _on_approve_clicked(self):
        note_text = self.note_edit.toPlainText().strip()
        self._submit_verdict(STATUS_APPROVED, note_text)

    def _on_retake_clicked(self):
        note_text = self.note_edit.toPlainText().strip()
        if not note_text:
            ans = QMessageBox.question(
                self, "Retake Note",
                "Submitting a Retake without revision notes makes it harder for the artist. Submit anyway?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if ans != QMessageBox.StandardButton.Yes:
                self.note_edit.setFocus()
                return

        self._submit_verdict(STATUS_RETAKE, note_text)

    def _submit_verdict(self, status: str, note_text: str):
        if not self.version or self.version.id <= 0:
            QMessageBox.warning(self, "Invalid Version", "Cannot update an unpersisted version.")
            return

        try:
            ok = self.store.update_version(self.version.id, status=status)
            if not ok:
                QMessageBox.critical(self, "Error", f"Failed to update version status to {status}.")
                return

            if note_text:
                self.store.add_note(
                    version_id=self.version.id,
                    text=note_text,
                    source=SENT_INTERNAL,
                    author=self.current_user,
                )

            self.version.status = status
            self.verdict_submitted.emit(self.version, status, note_text)
            self.note_edit.clear()

            # Advance to next item or close if at end
            if self.current_index < len(self.queue_versions) - 1:
                self._on_next_clicked()
            else:
                QMessageBox.information(
                    self, "Queue Complete",
                    f"Version {self.version.version_name} set to {status}. You have reached the end of the review queue."
                )
                self.accept()
        except Exception as exc:
            logging.exception("Failed to submit verdict: %s", exc)
            QMessageBox.critical(self, "Error", f"An unexpected error occurred: {exc}")

    def _on_previous_clicked(self):
        if self.current_index > 0:
            self.current_index -= 1
            self._load_version(self.queue_versions[self.current_index])

    def _on_next_clicked(self):
        if self.current_index < len(self.queue_versions) - 1:
            self.current_index += 1
            self._load_version(self.queue_versions[self.current_index])

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.close()
        else:
            super().keyPressEvent(event)

    def closeEvent(self, event):
        if self.player:
            self.player.stop_media()
        super().closeEvent(event)
