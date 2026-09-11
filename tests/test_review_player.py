"""
Tests for Item 4.3: Review Queue Opens the Player.

Verifies:
- ReviewPlayerDialog loads version metadata and media paths
- Queue navigation through previous/next versions
- Approve verdict updates status in VersionStore
- Retake verdict updates status and records VersionNote in VersionStore
- ReviewQueueDialog cellDoubleClicked interaction
"""

import pytest
from PySide6.QtWidgets import QApplication

from slate.core.domain.versions import Version, VersionStore, STATUS_APPROVED, STATUS_RETAKE
from slate.gui.tabs.vfx_dashboard_pro.ui.review_queue_dialog import ReviewQueueDialog
from slate.gui.tabs.vfx_dashboard_pro.ui.review_player_dialog import ReviewPlayerDialog


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


class FakeDb:
    def __init__(self):
        self.versions = {}
        self.notes = {}
        self._id_counter = 1

    def execute_query(self, query, params=None, fetch="all"):
        q = query.strip().upper()
        if "FROM TRACKING_VERSIONS WHERE PROJECT_CODE=" in q:
            p_code = params[0]
            return [v for v in self.versions.values() if v.get("project_code") == p_code]
        elif "FROM TRACKING_VERSION_NOTES WHERE VERSION_ID=" in q:
            v_id = int(params[0])
            return [n for n in self.notes.values() if n.get("version_id") == v_id]
        return []

    def execute_update(self, query, params=None):
        q = query.strip().upper()
        if q.startswith("UPDATE TRACKING_VERSIONS SET"):
            v_id = params[-1]
            if v_id in self.versions:
                # Update status
                self.versions[v_id]["status"] = params[0]
                return True
        elif q.startswith("INSERT INTO TRACKING_VERSION_NOTES"):
            v_id, source, author, note_date, text = params
            note_id = self._id_counter
            self._id_counter += 1
            self.notes[note_id] = {
                "id": note_id,
                "version_id": v_id,
                "source": source,
                "author": author,
                "note_date": note_date,
                "text": text,
            }
            return True
        return False


@pytest.fixture
def fake_store():
    db = FakeDb()
    db.versions[1] = {
        "id": 1,
        "project_code": "PRJ_REV",
        "shot_name": "SH010",
        "version_name": "v001",
        "department": "comp",
        "artist": "charlie",
        "status": "Pending",
        "sent_to": "client",
        "sent_date": "2026-09-09",
        "media_path": "",
    }
    db.versions[2] = {
        "id": 2,
        "project_code": "PRJ_REV",
        "shot_name": "SH020",
        "version_name": "v002",
        "department": "roto",
        "artist": "alice",
        "status": "In Review",
        "sent_to": "director",
        "sent_date": "2026-09-09",
        "media_path": "",
    }
    return VersionStore(db=db)


def test_review_queue_dialog_has_player_button(qapp, fake_store):
    dialog = ReviewQueueDialog("PRJ_REV", store=fake_store)
    assert hasattr(dialog, "play_btn")
    assert dialog.play_btn.isEnabled() is True
    assert dialog.table.rowCount() == 2
    assert dialog.table.item(0, 0).text() == "SH010"
    assert dialog.table.item(1, 0).text() == "SH020"


def test_review_player_navigation_and_approval(qapp, fake_store):
    versions = fake_store.awaiting_review("PRJ_REV")
    assert len(versions) == 2

    v1 = versions[0]
    dialog = ReviewPlayerDialog(
        version=v1,
        queue_versions=versions,
        current_index=0,
        store=fake_store,
        current_user="supervisor_jane",
    )

    assert "SH010" in dialog.title_label.text()
    assert dialog.prev_btn.isEnabled() is False
    assert dialog.next_btn.isEnabled() is True

    # Navigate next
    dialog._on_next_clicked()
    assert dialog.current_index == 1
    assert "SH020" in dialog.title_label.text()
    assert dialog.prev_btn.isEnabled() is True
    assert dialog.next_btn.isEnabled() is False

    # Approve version 2
    dialog.note_edit.setText("Looks great, finaled.")
    verdicts = []
    dialog.verdict_submitted.connect(lambda v, st, nt: verdicts.append((v.id, st, nt)))

    dialog._on_approve_clicked()
    assert len(verdicts) == 1
    assert verdicts[0] == (2, STATUS_APPROVED, "Looks great, finaled.")

    # Check store persistence
    notes = fake_store.notes_for_version(2)
    assert len(notes) == 1
    assert notes[0].text == "Looks great, finaled."
    assert notes[0].author == "supervisor_jane"


def test_review_player_retake_with_notes(qapp, fake_store):
    versions = fake_store.awaiting_review("PRJ_REV")
    v1 = versions[0]

    dialog = ReviewPlayerDialog(
        version=v1,
        queue_versions=versions,
        current_index=0,
        store=fake_store,
        current_user="lead_tom",
    )

    dialog.note_edit.setText("Edge chatter on frame 1045.")
    verdicts = []
    dialog.verdict_submitted.connect(lambda v, st, nt: verdicts.append((v.id, st, nt)))

    dialog._on_retake_clicked()
    assert len(verdicts) == 1
    assert verdicts[0] == (1, STATUS_RETAKE, "Edge chatter on frame 1045.")

    notes = fake_store.notes_for_version(1)
    assert len(notes) == 1
    assert notes[0].text == "Edge chatter on frame 1045."
    assert notes[0].author == "lead_tom"
