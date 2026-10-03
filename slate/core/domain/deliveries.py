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

from slate.core.infra.migrations.delivery_batches import apply_migration


@dataclass
class DeliveryItem:
    id: int = -1
    delivery_id: int = -1
    version_id: int = -1
    status_at_delivery: str = ""
    reel: str = ""
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
            reel=str(row.get("reel") or ""),
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

    def __init__(self, db=None, roles=None):
        if db is None:
            from slate.core.infra.database_manager import database_manager
            db = database_manager
        self.db = db
        # The acting person's roles. When given, making or deleting a package
        # needs production rights (dashboard_write, not department-scoped):
        # an artist could create delivery packages for the client.
        self.roles = roles
        # Why the last create was refused.
        self.last_error = ""
        self._ensure_schema()

    def can_manage(self) -> bool:
        if self.roles is None:
            return True
        from slate.core.domain.access import can_edit_dashboard, is_department_scoped
        return can_edit_dashboard(self.roles) and not is_department_scoped(self.roles)

    def _require_manage(self):
        if not self.can_manage():
            raise PermissionError("You don't have permission to create or delete delivery packages.")

    def _ensure_schema(self):
        try:
            apply_migration(self.db)
        except Exception as exc:
            logging.debug("Delivery schema check warning: %s", exc)

    def create_delivery(self, project_code: str, name: str, version_ids: List[int],
                        recipient: str = "", notes: str = "", created_by: str = "",
                        delivery_date: str = "") -> Optional[Delivery]:
        """
        Create a delivery package with these versions - all of it or nothing
        (a failure half-way left a partial package behind). Returns None and
        sets last_error when it was not created.
        """
        self._require_manage()
        self.last_error = ""
        project_code = str(project_code or "").strip()
        name = str(name or "").strip()
        if not project_code or not name or not version_ids:
            self.last_error = "A package needs a name and at least one version."
            return None
        if not delivery_date:
            delivery_date = date.today().isoformat()

        from slate.core.infra.transaction import atomic
        try:
            with atomic(self.db) as tx:
                if tx.one("SELECT id FROM tracking_deliveries WHERE project_code=%s AND name=%s",
                          (project_code, name)):
                    self.last_error = f"There is already a package called {name}."
                    return None
                # Resolve the versions first: a stale selection must not put
                # blank rows in a document that goes to a client.
                resolved = []
                for v_id in version_ids:
                    row = tx.one("SELECT id, status FROM tracking_versions WHERE id=%s AND project_code=%s",
                                 (int(v_id), project_code))
                    if row:
                        resolved.append((int(v_id), str(row.get("status") or "")))
                    else:
                        logging.warning("Skipping version %s: not on project %s", v_id, project_code)
                if not resolved:
                    self.last_error = "None of the selected versions exist any more."
                    return None
                created = tx.write(
                    "INSERT INTO tracking_deliveries "
                    "(project_code, name, recipient, delivery_date, notes, created_by) "
                    "VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
                    (project_code, name, recipient, delivery_date, notes, created_by))
                delivery_id = int(created.last_id)
                for v_id, status in resolved:
                    tx.write("INSERT INTO tracking_delivery_items (delivery_id, version_id, status_at_delivery) "
                             "VALUES (%s, %s, %s)", (delivery_id, v_id, status))
        except Exception as exc:
            logging.exception("Failed to create delivery %s: %s", name, exc)
            self.last_error = f"The package could not be saved: {exc}"
            return None
        return self.get_delivery(delivery_id)

    def next_free_name(self, project_code: str, day: Optional[date] = None) -> str:
        """DEL_<yyyymmdd>_NN with the first number nobody has used today."""
        stem = f"DEL_{(day or date.today()).strftime('%Y%m%d')}_"
        try:
            taken = {d.name for d in self.list_deliveries(project_code)}
        except Exception:
            taken = set()
        number = 1
        while f"{stem}{number:02d}" in taken:
            number += 1
        return f"{stem}{number:02d}"

    def list_deliveries(self, project_code: str) -> List[Delivery]:
        """Every package of a project, newest first. A failed read raises."""
        rows = self.db.execute_query(
            "SELECT * FROM tracking_deliveries WHERE project_code=%s ORDER BY id DESC",
            (project_code,), fetch="all")
        if rows is None:
            raise RuntimeError("The delivery packages could not be read.")
        return [Delivery.from_row(dict(r)) for r in rows]

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
            items_sql = """
                SELECT i.id, i.delivery_id, i.version_id, i.status_at_delivery,
                       v.reel, v.shot_name, v.version_name, v.department, v.media_path
                FROM tracking_delivery_items i
                LEFT JOIN tracking_versions v ON i.version_id = v.id
                WHERE i.delivery_id = %s
                ORDER BY v.reel, v.shot_name, v.version_name
            """
            item_rows = self.db.execute_query(items_sql, (int(delivery_id),), fetch="all") or []
            delivery.items = [DeliveryItem.from_row(dict(r)) for r in item_rows]
            return delivery
        except Exception as exc:
            logging.exception("Could not fetch delivery %s: %s", delivery_id, exc)
            return None

    def delete_delivery(self, delivery_id: int) -> bool:
        """Delete a package and its items, together."""
        self._require_manage()
        if not delivery_id or int(delivery_id) <= 0:
            return False
        from slate.core.infra.transaction import atomic
        try:
            with atomic(self.db) as tx:
                tx.write("DELETE FROM tracking_delivery_items WHERE delivery_id=%s", (int(delivery_id),))
                tx.write("DELETE FROM tracking_deliveries WHERE id=%s", (int(delivery_id),), expect_rows=True)
            return True
        except Exception as exc:
            logging.exception("Could not delete delivery %s: %s", delivery_id, exc)
            return False

    def generate_delivery_note(self, delivery_id: int) -> str:
        """
        An email-ready note: dates as people write them, department names, the
        project's name, and one line per version (tab-separated, so it lines up
        in any mail client and pastes into a sheet).
        """
        from slate.core.domain.dates import format_date
        from slate.core.domain.versions import department_label
        delivery = self.get_delivery(delivery_id)
        if not delivery:
            return "Delivery not found."
        project = self.db.execute_query("SELECT name FROM tracking_projects WHERE code=%s",
                                        (delivery.project_code,), fetch="one")
        project_name = (dict(project).get("name") if project else "") or ""
        project_text = f"{delivery.project_code} - {project_name}" if project_name and \
            project_name != delivery.project_code else delivery.project_code

        lines = [
            f"Delivery note: {delivery.name}",
            f"Project: {project_text}",
            f"Date: {format_date(delivery.delivery_date) or delivery.delivery_date}",
            f"To: {delivery.recipient or 'the client'}",
            f"Prepared by: {delivery.created_by or 'production'}",
            "",
            f"{len(delivery.items)} version{'s' if len(delivery.items) != 1 else ''}:",
            "Reel\tShot\tVersion\tDepartment\tStatus\tMedia",
        ]
        for item in delivery.items:
            lines.append("\t".join([item.reel or "-", item.shot_name, item.version_name,
                                    department_label(item.department) or "-", item.status_at_delivery or "-",
                                    item.media_path or "no media recorded"]))
        if delivery.notes:
            lines.extend(["", "Notes:", delivery.notes])
        return "\n".join(lines)
