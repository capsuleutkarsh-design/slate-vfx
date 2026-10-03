"""
Delivery packages: creating, reading, deleting and the delivery note, on a real
(SQLite) database - a package and its items are written together or not at all.
"""

import pytest

from slate.core.domain.deliveries import DeliveryStore
from slate.core.domain.versions import STATUS_APPROVED, VersionStore

PROJECT = "PRJ_DEL"


@pytest.fixture
def stores(mock_db):
    versions = VersionStore(db=mock_db)
    v1 = versions.add_version(PROJECT, "SH010", version_name="v004", department="comp",
                              media_path="/renders/SH010_v004.mov", reel="R01")
    v2 = versions.add_version(PROJECT, "SH020", version_name="v002", department="roto",
                              media_path="/renders/SH020_v002.mov", reel="R01")
    for v in (v1, v2):
        versions.update_version(v.id, status=STATUS_APPROVED)
    return DeliveryStore(db=mock_db), versions, v1, v2


def test_create_and_get_delivery(stores):
    store, _versions, v1, v2 = stores
    delivery = store.create_delivery(PROJECT, "DEL_20260909_01", [v1.id, v2.id],
                                     recipient="Client Editorial", notes="Finals.",
                                     created_by="producer_mark")
    assert delivery is not None
    assert delivery.recipient == "Client Editorial"
    assert [i.shot_name for i in delivery.items] == ["SH010", "SH020"]
    assert delivery.items[0].reel == "R01"


def test_a_name_used_already_is_refused_and_the_next_one_is_suggested(stores):
    """DSH2-080."""
    store, _versions, v1, _v2 = stores
    from datetime import date
    day = date(2026, 10, 3)
    first = store.next_free_name(PROJECT, day)
    assert first == "DEL_20261003_01"
    assert store.create_delivery(PROJECT, first, [v1.id]) is not None
    assert store.next_free_name(PROJECT, day) == "DEL_20261003_02"
    assert store.create_delivery(PROJECT, first, [v1.id]) is None
    assert "already" in store.last_error


def test_a_failure_half_way_leaves_nothing_behind(stores, monkeypatch):
    """DSH2-030: the package row and its items are one transaction."""
    store, _versions, v1, v2 = stores
    from slate.core.infra import transaction

    real = transaction.AtomicUnit.write

    def failing(self, sql, params=None, **kw):
        if "tracking_delivery_items" in sql:
            raise transaction.DatabaseWriteError("refused")
        return real(self, sql, params, **kw)
    monkeypatch.setattr(transaction.AtomicUnit, "write", failing)
    assert store.create_delivery(PROJECT, "DEL_X", [v1.id, v2.id]) is None
    monkeypatch.setattr(transaction.AtomicUnit, "write", real)
    assert store.list_deliveries(PROJECT) == []


def test_generate_delivery_note(stores, mock_db):
    """DSH2-082/097: dates and department names as people read them, one line per version."""
    store, _versions, v1, _v2 = stores
    mock_db.save_tracking_project(PROJECT, "Delivery Show", "{}")
    delivery = store.create_delivery(PROJECT, "DEL_20260909_02", [v1.id], recipient="Director Review",
                                     notes="Shot 10 slapcomp.", delivery_date="2026-09-09")
    note = store.generate_delivery_note(delivery.id)
    assert "Delivery note: DEL_20260909_02" in note
    assert "PRJ_DEL - Delivery Show" in note
    assert "9 Sep 2026" in note
    assert "R01\tSH010\tv004\tComp\tApproved\t/renders/SH010_v004.mov" in note
    assert "Prepared by" in note and "Shot 10 slapcomp." in note


def test_delete_delivery(stores):
    store, versions, v1, v2 = stores
    delivery = store.create_delivery(PROJECT, "DEL_TO_DELETE", [v1.id, v2.id])
    assert store.delete_delivery(delivery.id) is True
    assert store.get_delivery(delivery.id) is None
    assert len(versions.list_for_shot(PROJECT, "SH010")) == 1     # versions untouched


def test_new_delivery_lists_the_latest_approved_versions(qtbot, stores):
    """DSH2-029."""
    from slate.gui.tabs.vfx_dashboard_pro.ui.delivery_batches_dialog import CreateDeliveryDialog
    store, versions, v1, _v2 = stores
    versions.add_version(PROJECT, "SH010", version_name="v005", department="comp", reel="R01")  # pending
    dialog = CreateDeliveryDialog(PROJECT, store=store, version_store=versions)
    qtbot.addWidget(dialog)
    shown = {dialog.versions_table.item(r, 3).text() for r in range(dialog.versions_table.rowCount())}
    assert shown == {"v004", "v002"}
    dialog.show_all.setChecked(True)
    assert dialog.versions_table.rowCount() == 3
