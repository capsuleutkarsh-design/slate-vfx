"""
Distributed lock coordinator for shared JSON writes.

Purpose:
- Serialize writes across multiple UT_VFX clients when files are on SMB/network
  shares (where plain lock-file semantics can be unreliable).
- Use PostgreSQL as the lock authority.
- Allow local in-process fallback only for non-network paths.
"""

from __future__ import annotations

import logging
import os
import socket
import time
from dataclasses import dataclass
from pathlib import Path
from threading import Lock, RLock
from typing import Dict, Optional
from uuid import uuid4


@dataclass
class LockHandle:
    lock_name: str
    mode: str  # "db" | "local"
    local_lock: Optional[RLock] = None


class SharedJsonWriteCoordinator:
    """
    Coordinates cross-client locks for shared JSON writes.

    DB lock table schema:
    - lock_name (PK)
    - owner_id
    - lease_until
    - updated_at
    """

    _local_lock_guard = Lock()
    _local_locks: Dict[str, RLock] = {}

    def __init__(
        self,
        db_manager=None,
        lock_table: str = "ut_vfx_json_write_locks",
        owner_id: Optional[str] = None,
        allow_local_fallback: bool = True,
    ):
        if db_manager is None:
            from .database_manager import database_manager

            db_manager = database_manager
        self.db = db_manager
        self.lock_table = lock_table
        self.allow_local_fallback = bool(allow_local_fallback)
        self.owner_id = owner_id or f"{socket.gethostname()}:{os.getpid()}:{uuid4().hex[:8]}"
        self._table_ready = False

    @classmethod
    def _get_local_lock(cls, lock_name: str) -> RLock:
        with cls._local_lock_guard:
            lock = cls._local_locks.get(lock_name)
            if lock is None:
                lock = RLock()
                cls._local_locks[lock_name] = lock
            return lock

    @staticmethod
    def _is_network_path(target_path: Optional[Path]) -> bool:
        if target_path is None:
            return False
        text = str(target_path).replace("\\", "/").strip().lower()
        if not text:
            return False
        # UNC shares and known mapped studio drive roots.
        if text.startswith("//"):
            return True
        if text.startswith("x:/"):
            return True
        return False

    def _ensure_lock_table(self) -> None:
        if self._table_ready:
            return
        query = f"""
            CREATE TABLE IF NOT EXISTS {self.lock_table} (
                lock_name TEXT PRIMARY KEY,
                owner_id TEXT NOT NULL,
                lease_until TIMESTAMPTZ NOT NULL,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        """
        migrate_query = f"""
            ALTER TABLE {self.lock_table} ADD COLUMN IF NOT EXISTS owner_id TEXT NOT NULL DEFAULT 'legacy';
        """
        with self.db.transaction() as conn:
            with conn.cursor() as cur:
                cur.execute(query)
                try:
                    cur.execute(migrate_query)
                except Exception as e:
                    logging.debug(f"Migration for {self.lock_table} failed or not required: {e}")
        self._table_ready = True

    def _try_acquire_db_lock(self, lock_name: str, lease_seconds: int) -> bool:
        if str(getattr(self.db, "active_mode", "")).lower() != "postgres":
            raise RuntimeError("Distributed DB lock requires postgres backend")
        self._ensure_lock_table()
        query = f"""
            INSERT INTO {self.lock_table} (lock_name, owner_id, lease_until, updated_at)
            VALUES (%s, %s, NOW() + (%s || ' seconds')::interval, NOW())
            ON CONFLICT (lock_name) DO UPDATE SET
                owner_id = EXCLUDED.owner_id,
                lease_until = EXCLUDED.lease_until,
                updated_at = NOW()
            WHERE {self.lock_table}.owner_id = EXCLUDED.owner_id
               OR {self.lock_table}.lease_until <= NOW()
            RETURNING owner_id
        """
        with self.db.transaction() as conn:
            with conn.cursor() as cur:
                cur.execute(query, (lock_name, self.owner_id, int(lease_seconds)))
                row = cur.fetchone()
                return bool(row and row[0] == self.owner_id)

    def acquire(
        self,
        lock_name: str,
        *,
        target_path: Optional[Path] = None,
        timeout_seconds: float = 15.0,
        lease_seconds: int = 120,
        poll_interval: float = 0.25,
    ) -> Optional[LockHandle]:
        """
        Acquire a lock for shared write operations.

        Returns:
            LockHandle on success, None on failure.
        """
        lock_name = str(lock_name or "").strip()
        if not lock_name:
            return None

        deadline = time.time() + max(1.0, float(timeout_seconds or 15.0))
        db_error = None

        db_mode = str(getattr(self.db, "active_mode", "")).lower()
        can_use_db_lock = db_mode == "postgres"

        while can_use_db_lock and time.time() < deadline:
            try:
                if self._try_acquire_db_lock(lock_name, lease_seconds=lease_seconds):
                    return LockHandle(lock_name=lock_name, mode="db")
            except Exception as exc:
                db_error = exc
                break

            time.sleep(max(0.05, float(poll_interval or 0.25)))

        force_local_fallback = not can_use_db_lock
        # Never use local fallback for network paths, as local threads locking a network
        # file does not prevent other machines from corrupting the write.
        if self.allow_local_fallback and not self._is_network_path(target_path):
            remaining = max(0.1, deadline - time.time())
            local_lock = self._get_local_lock(lock_name)
            acquired = local_lock.acquire(timeout=remaining)
            if acquired:
                logging.warning(
                    "SharedJsonWriteCoordinator using local fallback lock for %s", lock_name
                )
                return LockHandle(lock_name=lock_name, mode="local", local_lock=local_lock)

        if db_error:
            logging.error(
                "Failed to acquire distributed lock '%s': %s",
                lock_name,
                db_error,
            )
        else:
            logging.error("Timeout acquiring distributed lock '%s'", lock_name)
        return None

    def release(self, handle: Optional[LockHandle]) -> None:
        if not handle:
            return

        if handle.mode == "local":
            if handle.local_lock:
                try:
                    handle.local_lock.release()
                except RuntimeError as exc:
                    logging.debug("Local lock release skipped: %s", exc)
            return

        if handle.mode != "db":
            return

        try:
            query = f"DELETE FROM {self.lock_table} WHERE lock_name=%s AND owner_id=%s"
            with self.db.transaction() as conn:
                with conn.cursor() as cur:
                    cur.execute(query, (handle.lock_name, self.owner_id))
        except Exception as exc:
            logging.warning(
                "Failed to release distributed lock '%s': %s",
                handle.lock_name,
                exc,
            )
