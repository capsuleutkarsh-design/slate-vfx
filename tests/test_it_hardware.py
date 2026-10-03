"""
The hardware inventory's rules (IT-002 ... IT-039), on SQLite and PostgreSQL.
"""

import json
from datetime import date, timedelta

import pytest

from slate.core.domain import hardware as hw
from slate.core.infra.hardware_repository import HardwareError, HardwareRepository


# ------------------------------------------------------------------- helpers

def test_ram_text_is_only_written_for_a_number():
    """IT-010."""
    assert hw.gb_text(None) == "" and hw.gb_text("") == "" and hw.gb_text("abc") == ""
    assert hw.gb_text(64) == "64 GB" and hw.gb_text("64") == "64 GB" and hw.gb_text(31.9) == "31.9 GB"
    assert hw.storage_text([{"Capacity_GB": 1000}, {"Capacity_GB": None}]) == "1000 GB"
    assert hw.gb_value("2 TB NVMe") == 2048 and hw.gb_value("64 GB") == 64 and hw.gb_value("") == 0


def test_missing_values_are_one_dash():
    """IT-013, IT-014."""
    for value in (None, "", "  ", "N/A", "None"):
        assert hw.cell(value) == hw.MISSING
    assert hw.cell("RTX 4090") == "RTX 4090"


def test_machine_names_are_hostnames():
    """IT-024."""
    assert hw.name_problem("WS-COMP-07") == ""
    assert hw.name_problem("a/b") and hw.name_problem("x" * 64) and hw.name_problem("")


def test_statuses_are_canonical():
    """IT-015, IT-016."""
    assert hw.normalise_status("active") == "Active"
    assert set(hw.END_OF_LIFE) == {"Retired", "Lost", "Disposed"}
    assert "Active" not in hw.ADD_STATUSES


# ---------------------------------------------------------------- repository

@pytest.fixture
def sqlite_db(tmp_path, monkeypatch):
    from slate.core.infra.global_config import GlobalConfig
    import slate.core.infra.database_manager as db_module
    from slate.core.infra.sqlite_manager import SQLiteManager

    monkeypatch.setattr(GlobalConfig, "get_db_mode", classmethod(lambda cls: "sqlite"))
    SQLiteManager._instance = None
    manager = db_module.DatabaseManager(db_path=str(tmp_path / "hw.db"))
    assert manager.active_mode == "sqlite"
    with db_module._manager_lock:
        previous = db_module._manager_instance
        db_module._manager_instance = manager
    try:
        yield manager
    finally:
        with db_module._manager_lock:
            db_module._manager_instance = previous
        SQLiteManager._instance = None


@pytest.fixture(params=["sqlite", "postgres"])
def repo(request):
    db = request.getfixturevalue("sqlite_db" if request.param == "sqlite" else "pg_db")
    db.execute_update("INSERT INTO ut_users (username, display_name, roles) VALUES (%s, %s, %s)",
                      ("artist02", "Meera Rao", '["Artist"]'))
    from slate.core.domain import people
    people.refresh()
    return HardwareRepository(db)


def _row(repo, name):
    return next(r for r in repo.machines() if r["machine_name"].lower() == name.lower())


def test_a_new_machine_is_never_active(repo):
    """IT-003."""
    repo.add("WS-01", {"cpu": "Xeon"}, status="Active")
    assert _row(repo, "WS-01")["status"] == hw.AVAILABLE


def test_blank_fields_are_stored_as_nothing(repo):
    """IT-013."""
    repo.add("WS-02", {"location": "", "cpu": " ", "gpu": "N/A"})
    row = _row(repo, "WS-02")
    assert row["location"] is None and row["cpu"] is None and row["gpu"] is None


def test_in_service_follows_the_ledger_and_repair_on_loan_collects(repo):
    """IT-004."""
    repo.add("WS-PAINT-002", {})
    repo.service.issue_machine("WS-PAINT-002", "artist02", "it")
    assert repo.status_after("WS-PAINT-002", hw.IN_SERVICE) == hw.ACTIVE
    with pytest.raises(HardwareError):
        repo.set_status("WS-PAINT-002", hw.REPAIR)
    assert _row(repo, "WS-PAINT-002")["status"] == hw.ACTIVE
    assert repo.set_status("WS-PAINT-002", hw.REPAIR, collect=True) == hw.REPAIR
    assert repo.holder("WS-PAINT-002") is None
    assert repo.set_status("WS-PAINT-002", hw.IN_SERVICE) == hw.AVAILABLE


def test_retired_machines_are_not_offered_for_issue(repo):
    """IT-016."""
    repo.add("OLD-01", {})
    repo.set_status("OLD-01", hw.RETIRED)
    assert "OLD-01" not in repo.service.available_machines()
    assert repo.counts(repo.machines())["retired"] == 1


def test_rename_moves_the_history(repo):
    """IT-025."""
    repo.add("WS-03", {})
    repo.service.issue_machine("WS-03", "artist02", "it")
    repo.service.return_machine("WS-03", "artist02")
    repo.add("WS-04", {})
    with pytest.raises(HardwareError):
        repo.rename("WS-03", "ws-04")
    assert repo.rename("WS-03", "WS-03B")
    assert len(repo.history("WS-03B")) == 1 and repo.history("WS-03") == []


def test_a_machine_with_history_is_retired_not_deleted(repo):
    """IT-037."""
    repo.add("WS-05", {})
    repo.service.issue_machine("WS-05", "artist02", "it")
    repo.service.return_machine("WS-05", "artist02")
    with pytest.raises(HardwareError) as refused:
        repo.delete("WS-05")
    assert "Retired" in str(refused.value)
    repo.add("TYPO-01", {})
    repo.delete("TYPO-01")
    assert not repo.exists("TYPO-01")


def test_history_lists_every_loan_newest_first(repo):
    """IT-033."""
    repo.add("WS-06", {})
    for _ in range(2):
        repo.service.issue_machine("WS-06", "artist02", "it")
        repo.service.return_machine("WS-06", "artist02")
    assert len(repo.history("WS-06")) == 2


def test_figures_count_loans_from_the_ledger(repo):
    """IT-029."""
    for name in ("A-1", "A-2", "A-3"):
        repo.add(name, {})
    repo.service.issue_machine("A-1", "artist02", "it")
    repo.set_status("A-2", hw.REPAIR)
    counts = repo.counts(repo.machines())
    assert counts == {"total": 3, "available": 1, "repair": 1, "on_loan": 1, "retired": 0}


def test_warranty_state():
    """IT-031."""
    today = date(2026, 10, 2)
    assert HardwareRepository.warranty_state(today - timedelta(days=1), today) == "expired"
    assert HardwareRepository.warranty_state(today + timedelta(days=30), today) == "soon"
    assert HardwareRepository.warranty_state(today + timedelta(days=300), today) == ""
    assert HardwareRepository.warranty_state(None, today) == ""


def test_type_serial_tag_and_dates_are_kept(repo):
    """IT-031, IT-032."""
    repo.add("LT-01", {"type": "Laptop", "serial_number": "SN123", "asset_tag": "KLC-0042",
                       "warranty_until": date(2027, 1, 31), "purchased_on": date(2024, 1, 31)})
    row = _row(repo, "LT-01")
    assert (row["type"], row["serial_number"], row["asset_tag"]) == ("Laptop", "SN123", "KLC-0042")
    assert str(row["warranty_until"])[:10] == "2027-01-31"


def test_sync_never_blanks_typed_specs_and_names_bad_reports(repo, tmp_path):
    """IT-010, IT-011, IT-012."""
    repo.add("ws-comp-001", {"cpu": "Xeon", "gpu": "A6000", "ram": "128 GB"})
    status = tmp_path / "status"
    status.mkdir()
    (status / "ws-comp-001.json").write_text(json.dumps({"ComputerName": "ws-comp-001", "CPU": "",
                                                         "GPU": "", "RAM_GB": None}))
    (status / "WS-LIVE-NEW.json").write_text(json.dumps({"ComputerName": "WS-LIVE-NEW", "CPU": "i9"}))
    (status / "broken.json").write_text("{not json")
    summary = repo.sync_from_reports(status)
    assert (summary["added"], summary["updated"], summary["unreadable"]) == (1, 1, ["broken.json"])
    kept = _row(repo, "ws-comp-001")
    assert (kept["cpu"], kept["gpu"], kept["ram"]) == ("Xeon", "A6000", "128 GB")
    new = _row(repo, "WS-LIVE-NEW")
    assert new["ram"] is None and new["location"] is None and new["status"] == hw.AVAILABLE


def test_the_one_time_repair_normalises_stored_values(repo):
    """IT-013, IT-015."""
    from slate.core.infra.migrations import it_schema
    db = repo.db
    db.execute_update("INSERT INTO hardware_inventory (machine_name, status, location) VALUES (%s, %s, %s)",
                      ("render-node-02", "active", "N/A"))
    it_schema.normalise_hardware(db)
    stored = db.execute_query("SELECT status FROM hardware_inventory WHERE machine_name = %s",
                              ("render-node-02",), fetch="one")
    assert dict(stored)["status"] == "Active"
    row = _row(repo, "render-node-02")
    # Nobody holds it on the ledger, so in service it reads Available (IT2-035).
    assert row["status"] == "Available" and row["location"] is None


def test_one_issuable_rule_for_hardware_and_joining(repo):
    """IT-016 regression, NEW-it-1: Repair (any case) and end of life are never offered."""
    assert not hw.can_be_issued("repair") and not hw.can_be_issued("Retired")
    assert hw.can_be_issued("Available") and hw.can_be_issued(None)
    for name, status in (("OK-1", "Available"), ("R-1", "repair"), ("D-1", "Disposed"), ("L-1", "Lost")):
        repo.db.execute_update("INSERT INTO hardware_inventory (machine_name, status) VALUES (%s, %s)",
                               (name, status))
    detail = [r["machine_name"] for r in repo.service.available_machines_detail()]
    assert detail == ["OK-1"] and repo.service.available_machines() == ["OK-1"]


def test_old_none_gb_values_read_and_store_as_nothing(repo):
    """IT-010: 'None GB' / ' GB' written by the old sync."""
    from slate.core.infra.migrations import it_schema
    assert hw.cell("None GB") == hw.MISSING and hw.cell(" GB") == hw.MISSING
    repo.db.execute_update("INSERT INTO hardware_inventory (machine_name, status, ram) VALUES (%s, %s, %s)",
                           ("OLD-RAM", "Available", "None GB"))
    it_schema.blank_junk_hardware_values(repo.db)
    assert _row(repo, "OLD-RAM")["ram"] is None



# ------------------------------------------------------------------ round 2

def test_the_ledger_alone_says_who_holds_a_machine(repo):
    """IT2-035: a typed-in owner is moved onto the ledger once, or left as a note."""
    from slate.core.infra.migrations import it_schema
    db = repo.db
    for name, owner in (("WS-ROTO-05", "artist02"), ("WS-ROTO-06", "Somebody Old")):
        db.execute_update("INSERT INTO hardware_inventory (machine_name, status, assigned_to) "
                          "VALUES (%s, 'Active', %s)", (name, owner))
    assert _row(repo, "WS-ROTO-05")["holder"] is None                  # not the typed copy
    it_schema.legacy_owners_to_loans(db)
    assert repo.holder("WS-ROTO-05") == "artist02"
    assert _row(repo, "WS-ROTO-05")["status"] == hw.ACTIVE
    assert repo.holder("WS-ROTO-06") is None and _row(repo, "WS-ROTO-06")["status"] == hw.AVAILABLE
    assert "WS-ROTO-06" in repo.service.available_machines()
    assert "WS-ROTO-05" not in repo.service.available_machines()


def test_the_figures_add_up_and_name_one_place_each(repo):
    """IT2-036: in repair while on loan counts once (on loan)."""
    repo.add("WS-1", {})
    repo.add("WS-2", {}, status=hw.REPAIR)
    repo.add("WS-3", {})
    repo.service.issue_machine("WS-3", "artist02", "it")
    repo.db.execute_update("UPDATE hardware_inventory SET status = 'Repair' WHERE machine_name = 'WS-3'")
    counts = repo.counts(repo.machines())
    assert counts == {"total": 3, "available": 1, "repair": 1, "on_loan": 1, "retired": 0}


def test_numbered_machines_sort_as_counted(repo):
    """IT2-043."""
    for name in ("WS-COMP-10", "WS-COMP-2", "WS-COMP-1"):
        repo.add(name, {})
    assert [r["machine_name"] for r in repo.machines()] == ["WS-COMP-1", "WS-COMP-2", "WS-COMP-10"]


def test_peripherals_need_a_name_not_a_hostname_and_sizes_read_in_tb():
    """IT2-047 / IT2-042."""
    assert hw.name_problem("Dell U2723QE #3")
    assert hw.name_problem("Dell U2723QE #3", "Monitor") == ""
    assert hw.size_text("9315 GB") == "9.1 TB" and hw.size_text("512 GB") == "512 GB"
    assert hw.size_text(None) == hw.MISSING


def test_status_changes_and_renames_are_in_the_history(repo):
    """IT2-049."""
    repo.add("WS-9", {})
    repo.set_status("WS-9", hw.RETIRED, by="it.sana")
    repo.rename("WS-9", "WS-9B", by="it.sana")
    whats = [e["what"] for e in repo.events("WS-9B")]
    assert whats == ["Renamed from WS-9", "Status set to Retired"]


def test_sync_names_bad_names_and_end_of_life_machines(repo, tmp_path):
    """IT2-038 / IT2-040."""
    import json
    repo.add("WS-OLD", {})
    repo.set_status("WS-OLD", hw.RETIRED)
    (tmp_path / "a.json").write_text(json.dumps({"ComputerName": "NEW BOX 7"}))
    (tmp_path / "b.json").write_text(json.dumps({"ComputerName": "WS-OLD", "CPU": "i7"}))
    summary = repo.sync_from_reports(tmp_path)
    assert summary["bad_names"] == ["NEW BOX 7"] and summary["unreadable"] == []
    assert summary["end_of_life"] == [("WS-OLD", hw.RETIRED)]
