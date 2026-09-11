"""
Delivery packages and submission tracking.

Work goes to clients in packages, but tracking previously only recorded
individual versions. A delivery package bundles approved versions together
with recipient, date, and client notes, and can generate clean delivery notes.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Dict, List, Optional

from ut_vfx.core.infra.migrations.delivery_batches import apply_migration


@dataclass
class DeliveryItem:
    id: int = -1
    delivery_id: int = -1
    version_id: int = -1
    status_at_delivery: str = ""
    shot_name: str = ""
    version_name: str = ""
    department: str = ""
    media_path: str = ""

    @classmethod
    def from_row(cls, row: dict) -> "DeliveryItem":
        return cls(
            id=int(row.get("id") or -1),
            delivery_id=int(row.get("delivery_id") or -1),
            version_id=int(row.get("version_id") or -1),
            status_at_delivery=str(row.get("status_at_delivery") or ""),
            shot_name=str(row.get("shot_name") or ""),
            version_name=str(row.get("version_name") or ""),
            department=str(row.get("department") or ""),
            media_path=str(row.get("media_path") or ""),
        )


@dataclass
class Delivery:
    id: int = -1
    project_code: str = ""
    name: str = ""
    recipient: str = ""
    delivery_date: str = ""
    notes: str = ""
    created_by: str = ""
    created_at: str = ""
    items: List[DeliveryItem] = field(default_factory=list)

    @classmethod
    def from_row(cls, row: dict) -> "Delivery":
        return cls(
            id=int(row.get("id") or -1),
            project_code=str(row.get("project_code") or ""),
            name=str(row.get("name") or ""),
            recipient=str(row.get("recipient") or ""),
            delivery_date=str(row.get("delivery_date") or ""),
            notes=str(row.get("notes") or ""),
            created_by=str(row.get("created_by") or ""),
            created_at=str(row.get("created_at") or ""),
        )


class DeliveryStore:
    """Reads and writes delivery packages."""

    def __init__(self, db=None):
        if db is None:
            from ut_vfx.core.infra.database_manager import database_manager
            db = database_manager
        self.db = db
        self._ensure_schema()

    def _ensure_schema(self):
        try:
            apply_migration(self.db)
        except Exception as exc:
            logging.debug("Delivery schema check warning: %s", exc)

    def create_delivery(self, project_code: str, name: str, version_ids: List[int],
                        recipient: str = "", notes: str = "", created_by: str = "",
                        delivery_date: str = "") -> Optional[Delivery]:
        """Create a delivery batch and attach the given version IDs."""
        project_code = str(project_code or "").strip()
        name = str(name or "").strip()
        if not project_code or not name or not version_ids:
            return None

        if not delivery_date:
            delivery_date = date.today().isoformat()

        try:
            # Resolve the versions first. A stale selection must not create a
            # delivery containing blank rows - this document goes to a client.
            resolved = []
            for v_id in version_ids:
                v_row = self.db.execute_query(
                    "SELECT id, status FROM tracking_versions "
                    "WHERE id=%s AND project_code=%s",
                    (int(v_id), project_code),
                    fetch="one",
                )
                if not v_row:
                    logging.warning(
                        "Skipping version %s: it does not exist on project %s",
                        v_id, project_code,
                    )
                    continue
                resolved.append((int(v_id), str(v_row.get("status") or "")))

            if not resolved:
                logging.error(
                    "Refusing to create delivery '%s': none of the selected "
                    "versions could be found.", name,
                )
                return None

            # 1. Insert master delivery record
            self.db.execute_update(
                "INSERT INTO tracking_deliveries "
                "(project_code, name, recipient, delivery_date, notes, created_by) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                (project_code, name, recipient, delivery_date, notes, created_by),
            )

            # Retrieve inserted ID
            row = self.db.execute_query(
                "SELECT id FROM tracking_deliveries "
                "WHERE project_code=%s AND name=%s ORDER BY id DESC",
                (project_code, name),
                fetch="one",
            )
            if not row:
                return None
            delivery_id = int(row.get("id"))

            # 2. Attach versions to the delivery package
            for v_id, curr_status in resolved:
                self.db.execute_update(
                    "INSERT INTO tracking_delivery_items (delivery_id, version_id, status_at_delivery) "
                    "VALUES (%s, %s, %s)",
                    (delivery_id, v_id, curr_status),
                )

            return self.get_delivery(delivery_id)
        except Exception as exc:
            logging.exception("Failed to create delivery %s: %s", name, exc)
            return None

    def list_deliveries(self, project_code: str) -> List[Delivery]:
        """List all deliveries for a project, newest first."""
        try:
            rows = self.db.execute_query(
                "SELECT * FROM tracking_deliveries WHERE project_code=%s ORDER BY id DESC",
                (project_code,),
                fetch="all",
            ) or []
            return [Delivery.from_row(dict(r)) for r in rows]
        except Exception as exc:
            logging.exception("Could not list deliveries for %s: %s", project_code, exc)
            return []

    def get_delivery(self, delivery_id: int) -> Optional[Delivery]:
        """Fetch delivery by ID along with its attached version items."""
        if not delivery_id or int(delivery_id) <= 0:
            return None
        try:
            row = self.db.execute_query(
                "SELECT * FROM tracking_deliveries WHERE id=%s",
                (int(delivery_id),),
                fetch="one",
            )
            if not row:
                return None

            delivery = Delivery.from_row(dict(row))

            # Fetch linked version details
            items_sql = """
                SELECT i.id, i.delivery_id, i.version_id, i.status_at_delivery,
                       v.shot_name, v.version_name, v.department, v.media_path
                FROM tracking_delivery_items i
                LEFT JOIN tracking_versions v ON i.version_id = v.id
                WHERE i.delivery_id = %s
                ORDER BY v.shot_name, v.version_name
            """
            item_rows = self.db.execute_query(items_sql, (int(delivery_id),), fetch="all") or []
            delivery.items = [DeliveryItem.from_row(dict(r)) for r in item_rows]
            return delivery
        except Exception as exc:
            logging.exception("Could not fetch delivery %s: %s", delivery_id, exc)
            return None

    def delete_delivery(self, delivery_id: int) -> bool:
        """Delete delivery and its associated item records."""
        if not delivery_id or int(delivery_id) <= 0:
            return False
        try:
            self.db.execute_update(
                "DELETE FROM tracking_delivery_items WHERE delivery_id=%s",
                (int(delivery_id),),
            )
            return bool(self.db.execute_update(
                "DELETE FROM tracking_deliveries WHERE id=%s",
                (int(delivery_id),),
            ))
        except Exception as exc:
            logging.exception("Could not delete delivery %s: %s", delivery_id, exc)
            return False

    def generate_delivery_note(self, delivery_id: int) -> str:
        """Format an email-ready delivery manifest for client communication."""
        delivery = self.get_delivery(delivery_id)
        if not delivery:
            return "Delivery not found."

        lines = [
            "=" * 78,
            f"DELIVERY NOTE: {delivery.name}",
            f"Project:    {delivery.project_code}",
            f"Date:       {delivery.delivery_date}",
            f"Recipient:  {delivery.recipient or 'Client VFX Team'}",
            f"Sent By:    {delivery.created_by or 'VFX Production'}",
            f"Package ID: #{delivery.id}",
            "=" * 78,
            "",
            f"Total Delivered Items: {len(delivery.items)} version(s)",
            "-" * 78,
            f"{'Shot':<16} | {'Version':<10} | {'Dept':<8} | {'Status':<12} | {'Media Path'}",
            "-" * 78,
        ]

        for item in delivery.items:
            path_str = item.media_path or "(none specified)"
            lines.append(
                f"{item.shot_name:<16} | {item.version_name:<10} | "
                f"{item.department:<8} | {item.status_at_delivery:<12} | {path_str}"
            )

        lines.append("-" * 78)
        if delivery.notes:
            lines.extend([
                "",
                "DELIVERY NOTES / INSTRUCTIONS:",
                delivery.notes,
            ])
        lines.append("=" * 78)

        return "\n".join(lines)
