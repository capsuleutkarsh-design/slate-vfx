
from ut_vfx.core.infra.consistency_protocol import (
    CrossStoreConsistencyProtocol,
    StoreAction,
)


def test_consistency_protocol_commits_all_actions(tmp_path):
    journal = tmp_path / "journal.json"
    protocol = CrossStoreConsistencyProtocol(journal_file=journal, scope="test_commit")

    state = []

    result = protocol.execute(
        operation="demo.commit",
        actions=[
            StoreAction("store_a", lambda: state.append("a")),
            StoreAction("store_b", lambda: state.append("b")),
        ],
        metadata={"case": "commit"},
    )

    assert result.success is True
    assert result.final_status == "committed"
    assert state == ["a", "b"]

    recent = protocol.get_recent_operations(limit=1)
    assert len(recent) == 1
    assert recent[0]["status"] == "committed"
    assert [s["status"] for s in recent[0]["stores"]] == ["applied", "applied"]


def test_consistency_protocol_rolls_back_on_failure(tmp_path):
    journal = tmp_path / "journal.json"
    protocol = CrossStoreConsistencyProtocol(journal_file=journal, scope="test_rollback")

    state = {"value": 0}

    def apply_a():
        state["value"] = 1

    def rollback_a():
        state["value"] = 0

    def apply_b():
        raise RuntimeError("boom")

    result = protocol.execute(
        operation="demo.rollback",
        actions=[
            StoreAction("store_a", apply_a, rollback_a),
            StoreAction("store_b", apply_b, None),
        ],
        metadata={"case": "rollback"},
    )

    assert result.success is False
    assert result.failed_store == "store_b"
    assert result.final_status == "rolled_back"
    assert state["value"] == 0

    recent = protocol.get_recent_operations(limit=1)
    assert len(recent) == 1
    assert recent[0]["status"] == "rolled_back"

    stores = {entry["name"]: entry for entry in recent[0]["stores"]}
    assert stores["store_b"]["status"] == "failed"
    assert stores["store_a"]["status"] == "rolled_back"
