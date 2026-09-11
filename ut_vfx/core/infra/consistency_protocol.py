"""
Cross-store consistency protocol with journaling and rollback support.

This provides a lightweight coordinator for workflows that write to multiple
stores (for example JSON + Postgres). It is intentionally application-level and
does not depend on database two-phase commit support.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Any, Callable, Dict, List, Optional
from uuid import uuid4

from ut_vfx.utils.safe_json import SafeJsonIO

from .global_config import GlobalConfig


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class StoreAction:
    """Single store action used by the consistency protocol."""

    store: str
    apply: Callable[[], None]
    rollback: Optional[Callable[[], None]] = None


@dataclass
class ConsistencyResult:
    """Result returned by the consistency coordinator."""

    operation_id: str
    success: bool
    final_status: str
    failed_store: Optional[str] = None
    error: str = ""


class CrossStoreConsistencyProtocol:
    """
    Coordinates ordered multi-store operations with rollback and operation log.

    Journal format:
    {
      "operations": [
        {
          "operation_id": "...",
          "operation": "user.add_or_update",
          "scope": "user_manager",
          "status": "running|committed|rolled_back|rollback_failed",
          "stores": [{"name":"users_json","status":"applied",...}, ...],
          ...
        }
      ]
    }
    """

    _journal_lock = RLock()

    def __init__(
        self,
        journal_file: Optional[Path] = None,
        max_entries: int = 500,
        scope: str = "default",
    ):
        self.scope = str(scope or "default")
        self.max_entries = max(50, int(max_entries or 500))
        self.journal_file = (
            Path(journal_file)
            if journal_file
            else GlobalConfig.local_cache_dir() / f"{self.scope}_consistency_journal.json"
        )

    def execute(
        self,
        operation: str,
        actions: List[StoreAction],
        metadata: Optional[Dict[str, Any]] = None,
    ) -> ConsistencyResult:
        """Execute an ordered cross-store operation."""
        if not actions:
            op_id = f"{self.scope}-{uuid4().hex[:12]}"
            return ConsistencyResult(operation_id=op_id, success=True, final_status="committed")

        op_id = f"{self.scope}-{uuid4().hex[:12]}"
        op_name = str(operation or "unknown")
        metadata = metadata or {}

        self._create_entry(op_id, op_name, actions, metadata)

        executed: List[StoreAction] = []
        failed_store: Optional[str] = None
        error_text = ""

        for action in actions:
            store_name = action.store
            self._set_store_status(op_id, store_name, "applying")
            try:
                action.apply()
            except Exception as exc:
                failed_store = store_name
                error_text = str(exc)
                self._set_store_status(op_id, store_name, "failed", error_text)
                break

            executed.append(action)
            self._set_store_status(op_id, store_name, "applied")

        if failed_store is None:
            self._set_final_status(op_id, "committed", error="")
            return ConsistencyResult(op_id, True, "committed")

        rollback_errors: List[str] = []
        for action in reversed(executed):
            store_name = action.store
            if action.rollback is None:
                msg = "rollback handler missing"
                rollback_errors.append(f"{store_name}: {msg}")
                self._set_store_status(op_id, store_name, "rollback_missing", msg)
                continue

            self._set_store_status(op_id, store_name, "rolling_back")
            try:
                action.rollback()
            except Exception as exc:
                msg = str(exc)
                rollback_errors.append(f"{store_name}: {msg}")
                self._set_store_status(op_id, store_name, "rollback_failed", msg)
            else:
                self._set_store_status(op_id, store_name, "rolled_back")

        final_status = "rolled_back" if not rollback_errors else "rollback_failed"
        if rollback_errors:
            error_text = f"{error_text}; rollback_errors={'; '.join(rollback_errors)}"

        self._set_final_status(
            op_id,
            final_status,
            failed_store=failed_store,
            error=error_text,
        )
        return ConsistencyResult(op_id, False, final_status, failed_store, error_text)

    def get_recent_operations(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Return recent operations from journal, newest first."""
        payload = SafeJsonIO.load_json(self.journal_file)
        operations = payload.get("operations", [])
        if not isinstance(operations, list):
            return []
        limit = max(1, int(limit or 50))
        return list(reversed(operations[-limit:]))

    def _create_entry(
        self,
        operation_id: str,
        operation: str,
        actions: List[StoreAction],
        metadata: Dict[str, Any],
    ) -> None:
        entry = {
            "operation_id": operation_id,
            "operation": operation,
            "scope": self.scope,
            "status": "running",
            "created_at": _utc_now_iso(),
            "completed_at": None,
            "failed_store": None,
            "error": "",
            "metadata": dict(metadata),
            "stores": [
                {"name": action.store, "status": "pending", "error": ""}
                for action in actions
            ],
        }

        def _mutate(data: Dict[str, Any]) -> None:
            ops = data.get("operations")
            if not isinstance(ops, list):
                ops = []
                data["operations"] = ops
            ops.append(entry)
            if len(ops) > self.max_entries:
                del ops[:-self.max_entries]
            data["updated_at"] = _utc_now_iso()

        self._mutate_journal(_mutate)

    def _set_store_status(
        self,
        operation_id: str,
        store_name: str,
        status: str,
        error: str = "",
    ) -> None:
        def _mutate(data: Dict[str, Any]) -> None:
            op = self._find_operation(data, operation_id)
            if op is None:
                return
            for store in op.get("stores", []):
                if store.get("name") == store_name:
                    store["status"] = status
                    store["error"] = error
                    break
            op["updated_at"] = _utc_now_iso()

        self._mutate_journal(_mutate)

    def _set_final_status(
        self,
        operation_id: str,
        final_status: str,
        failed_store: Optional[str] = None,
        error: str = "",
    ) -> None:
        def _mutate(data: Dict[str, Any]) -> None:
            op = self._find_operation(data, operation_id)
            if op is None:
                return
            op["status"] = final_status
            op["failed_store"] = failed_store
            op["error"] = error
            op["completed_at"] = _utc_now_iso()
            op["updated_at"] = _utc_now_iso()

        self._mutate_journal(_mutate)

    @staticmethod
    def _find_operation(data: Dict[str, Any], operation_id: str) -> Optional[Dict[str, Any]]:
        operations = data.get("operations", [])
        if not isinstance(operations, list):
            return None
        for op in reversed(operations):
            if op.get("operation_id") == operation_id:
                return op
        return None

    def _mutate_journal(self, mutator: Callable[[Dict[str, Any]], None]) -> None:
        with self._journal_lock:
            success = SafeJsonIO.update_json(self.journal_file, mutator)
            if not success:
                logging.warning("Consistency journal update failed: %s", self.journal_file)
