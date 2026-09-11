"""
Tests for Item 4.4: Delivery Batches.

Verifies:
- Creation of delivery packages with linked versions
- Reading delivery items and metadata
- Delivery note text generation for client emails
- Cascade deletion of delivery items without deleting the versions themselves
"""

import pytest
from PySide6.QtWidgets import QApplication

from slate.core.domain.deliveries import DeliveryStore, Delivery
from slate.core.domain.versions import VersionStore


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


class FakeDb:
    def __init__(self):
        self.deliveries = {}
        self.delivery_items = {}
        self.versions = {}
        self._d_counter = 1
        self._item_counter = 1

    def execute_query(self, query, params=None, fetch="all"):
        q = query.strip().upper()
        if "FROM TRACKING_DELIVERIES WHERE PROJECT_CODE=" in q:
            p_code = params[0]
            matches = [d for d in self.deliveries.values() if d.get("project_code") == p_code]
            if "ORDER BY ID DESC" in q:
                matches.sort(key=lambda d: d.get("id", 0), reverse=True)
            if fetch == "one":
                return matches[0] if matches else None
            return matches
        elif "FROM TRACKING_DELIVERIES WHERE ID=" in q:
            d_id = int(params[0])
            d = self.deliveries.get(d_id)
            return d if fetch == "one" else ([d] if d else [])
        elif "FROM TRACKING_DELIVERY_ITEMS" in q:
            d_id = int(params[0])
            res = []
            for item in self.delivery_items.values():
                if item.get("delivery_id") == d_id:
                    v_id = item.get("version_id")
                    v = self.versions.get(v_id, {})
                    merged = dict(item)
                    merged["shot_name"] = v.get("shot_name", "")
                    merged["version_name"] = v.get("version_name", "")
                    merged["department"] = v.get("department", "")
                    merged["media_path"] = v.get("media_path", "")
                    res.append(merged)
            return res
        elif "FROM TRACKING_VERSIONS WHERE ID=" in q:
            v_id = int(params[0])
            v = self.versions.get(v_id)
            return v if fetch == "one" else ([v] if v else [])
        elif "FROM TRACKING_VERSIONS WHERE PROJECT_CODE=" in q:
            p_code = params[0]
            return [v for v in self.versions.values() if v.get("project_code") == p_code]
        return []

    def execute_update(self, query, params=None):
        q = query.strip().upper()
        if q.startswith("INSERT INTO TRACKING_DELIVERIES"):
            p_code, name, recip, d_date, notes, created_by = params
            d_id = self._d_counter
            self._d_counter += 1
            self.deliveries[d_id] = {
                "id": d_id,
                "project_code": p_code,
                "name": name,
                "recipient": recip,
                "delivery_date": d_date,
                "notes": notes,
                "created_by": created_by,
                "created_at": "2026-09-09 16:00:00",
            }
            return True
        elif q.startswith("INSERT INTO TRACKING_DELIVERY_ITEMS"):
            d_id, v_id, status_at_delivery = params
            item_id = self._item_counter
            self._item_counter += 1
            self.delivery_items[item_id] = {
                "id": item_id,
                "delivery_id": d_id,
                "version_id": v_id,
                "status_at_delivery": status_at_delivery,
            }
            return True
        elif q.startswith("DELETE FROM TRACKING_DELIVERY_ITEMS"):
            d_id = int(params[0])
            to_del = [k for k, v in self.delivery_items.items() if v.get("delivery_id") == d_id]
            for k in to_del:
                del self.delivery_items[k]
            return True
        elif q.startswith("DELETE FROM TRACKING_DELIVERIES"):
            d_id = int(params[0])
            if d_id in self.deliveries:
                del self.deliveries[d_id]
                return True
        return False


@pytest.fixture
def fake_delivery_store():
    db = FakeDb()
    db.versions[1] = {
        "id": 1,
        "project_code": "PRJ_DEL",
        "shot_name": "SH010",
        "version_name": "v004",
        "department": "comp",
        "status": "Approved",
        "media_path": "/renders/SH010_v004.mov",
    }
    db.versions[2] = {
        "id": 2,
        "project_code": "PRJ_DEL",
        "shot_name": "SH020",
        "version_name": "v002",
        "department": "roto",
        "status": "Approved",
        "media_path": "/renders/SH020_v002.mov",
    }
    return DeliveryStore(db=db)


def test_create_and_get_delivery(fake_delivery_store):
    store = fake_delivery_store
    delivery = store.create_delivery(
        project_code="PRJ_DEL",
        name="DEL_20260909_01",
        version_ids=[1, 2],
        recipient="Client Editorial",
        notes="Final composites approved by supervisor.",
        created_by="producer_mark",
    )

    assert delivery is not None
    assert delivery.name == "DEL_20260909_01"
    assert delivery.recipient == "Client Editorial"
    assert len(delivery.items) == 2
    assert delivery.items[0].shot_name == "SH010"
    assert delivery.items[0].version_name == "v004"
    assert delivery.items[1].shot_name == "SH020"


def test_generate_delivery_note(fake_delivery_store):
    store = fake_delivery_store
    delivery = store.create_delivery(
        project_code="PRJ_DEL",
        name="DEL_20260909_02",
        version_ids=[1],
        recipient="Director Review",
        notes="Shot 10 slapcomp.",
    )

    note = store.generate_delivery_note(delivery.id)
    assert "DELIVERY NOTE: DEL_20260909_02" in note
    assert "SH010" in note
    assert "v004" in note
    assert "/renders/SH010_v004.mov" in note
    assert "Shot 10 slapcomp." in note


def test_delete_delivery(fake_delivery_store):
    store = fake_delivery_store
    delivery = store.create_delivery(
        project_code="PRJ_DEL",
        name="DEL_TO_DELETE",
        version_ids=[1, 2],
    )

    d_id = delivery.id
    assert store.get_delivery(d_id) is not None
    assert store.delete_delivery(d_id) is True
    assert store.get_delivery(d_id) is None

    # Verify versions are untouched
    assert 1 in store.db.versions
    assert 2 in store.db.versions
