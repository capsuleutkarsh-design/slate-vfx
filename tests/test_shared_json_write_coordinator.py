from contextlib import contextmanager
from pathlib import Path

from slate.core.infra.shared_json_write_coordinator import SharedJsonWriteCoordinator


class _BrokenDB:
    @contextmanager
    def transaction(self):
        raise RuntimeError("db unavailable")
        yield


class _FakeCursor:
    def __init__(self, state):
        self.state = state
        self._row = None

    def execute(self, query, params=None):
        q = " ".join(str(query).strip().lower().split())
        if q.startswith("create table"):
            self._row = None
            return
        if q.startswith("insert into") and "returning owner_id" in q:
            lock_name, owner_id, _lease_seconds = params
            current_owner = self.state.get(lock_name)
            if current_owner is None or current_owner == owner_id:
                self.state[lock_name] = owner_id
                self._row = (owner_id,)
            else:
                self._row = None
            return
        if q.startswith("delete from"):
            lock_name, owner_id = params
            if self.state.get(lock_name) == owner_id:
                del self.state[lock_name]
            self._row = None

    def fetchone(self):
        return self._row

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class _FakeConnection:
    def __init__(self, state):
        self.state = state

    def cursor(self):
        return _FakeCursor(self.state)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class _FakeDB:
    def __init__(self):
        self.state = {}
        self.active_mode = "postgres"

    @contextmanager
    def transaction(self):
        yield _FakeConnection(self.state)


# A path on shared storage. UNC rather than a mapped letter, because a
# mapped letter is only shared storage on the studio that mapped it - which
# is exactly the assumption this coordinator used to make.
SHARED_PATH = "//studio-server/slate/Slate_Central/Config/users.json"


def test_local_fallback_lock_when_db_unavailable(tmp_path):
    coordinator = SharedJsonWriteCoordinator(
        db_manager=_BrokenDB(),
        owner_id="owner-a",
        allow_local_fallback=True,
    )
    handle = coordinator.acquire(
        "users_lock",
        target_path=tmp_path / "users.json",
        timeout_seconds=1.0,
    )
    assert handle is not None
    assert handle.mode == "local"
    coordinator.release(handle)


def test_network_path_requires_distributed_lock_when_db_unavailable():
    coordinator = SharedJsonWriteCoordinator(
        db_manager=_BrokenDB(),
        owner_id="owner-a",
        allow_local_fallback=True,
    )
    handle = coordinator.acquire(
        "users_lock",
        target_path=Path(SHARED_PATH),
        timeout_seconds=1.0,
    )
    assert handle is None


def test_db_lock_acquire_and_release():
    fake_db = _FakeDB()
    coordinator = SharedJsonWriteCoordinator(
        db_manager=fake_db,
        owner_id="owner-a",
        allow_local_fallback=False,
    )
    handle = coordinator.acquire(
        "users_lock",
        target_path=Path(SHARED_PATH),
        timeout_seconds=1.0,
    )
    assert handle is not None
    assert handle.mode == "db"
    coordinator.release(handle)

    # Lock should be released and acquirable by another owner.
    coordinator_b = SharedJsonWriteCoordinator(
        db_manager=fake_db,
        owner_id="owner-b",
        allow_local_fallback=False,
    )
    handle_b = coordinator_b.acquire(
        "users_lock",
        target_path=Path(SHARED_PATH),
        timeout_seconds=1.0,
    )
    assert handle_b is not None
    assert handle_b.mode == "db"
    coordinator_b.release(handle_b)


def test_a_studio_folder_on_a_mapped_drive_is_shared_storage(monkeypatch):
    """
    The coordinator used to decide this by asking whether the path began with
    "x:/". Any studio whose share is on another letter had its shared files
    written under a local lock, so two machines could write the same file at
    the same time and neither knew.
    """
    from slate.core.infra import studio_paths

    monkeypatch.setattr(studio_paths, "_global_setting",
                        lambda key: r"Q:\Studio\Slate_Central"
                        if key == "SERVER_ROOT" else "")
    monkeypatch.setattr(studio_paths, "_machine_setting", lambda key: "")
    for name in studio_paths.STUDIO_ROOT_VARS:
        monkeypatch.delenv(name, raising=False)

    coordinator = SharedJsonWriteCoordinator(
        db_manager=_BrokenDB(),
        owner_id="owner-a",
        allow_local_fallback=True,
    )
    handle = coordinator.acquire(
        "users_lock",
        target_path=Path(r"Q:\Studio\Slate_Central\Config\users.json"),
        timeout_seconds=1.0,
    )
    assert handle is None, \
        "a shared file must not be written under a lock only this machine sees"


def test_a_local_path_under_no_studio_folder_is_not_shared(monkeypatch, tmp_path):
    from slate.core.infra import studio_paths

    monkeypatch.setattr(studio_paths, "_global_setting", lambda key: "")
    monkeypatch.setattr(studio_paths, "_machine_setting", lambda key: "")
    for name in studio_paths.STUDIO_ROOT_VARS:
        monkeypatch.delenv(name, raising=False)

    coordinator = SharedJsonWriteCoordinator(
        db_manager=_BrokenDB(),
        owner_id="owner-a",
        allow_local_fallback=True,
    )
    handle = coordinator.acquire(
        "users_lock",
        target_path=tmp_path / "users.json",
        timeout_seconds=1.0,
    )
    assert handle is not None and handle.mode == "local"
    coordinator.release(handle)
