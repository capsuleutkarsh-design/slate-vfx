"""
Everything Bidding reads and writes.

The tab used to write its own SQL from spin-box floats: a bid's day rate was
never stored (so Edit re-priced it at the default), its shot count was re-read
live from the tracker (so editing an approved bid silently changed its price),
changing the project in Edit saved the new project's shot count under the old
project, Delete removed the first of several selected bids for good, and
nothing recorded who made, sent or decided a bid.

Here a bid is a header plus its line items (prod_bid_lines), priced by
core/domain/bidding.price_bid in Decimal, saved header-and-lines in one
transaction, and changed only while it is a Draft - a sent or decided bid gets
a new revision instead. Every change is in the change history.

    repo = BidRepository(roles=["Production Head"], username="priya")
    repo.list(include_archived=False, all_revisions=False) -> [Bid]
    repo.get(id) / repo.lines(id)
    repo.create(bid, lines) -> id          repo.update(bid, lines)
    repo.revise(id) -> new id              repo.duplicate_to_project(id, code)
    repo.set_status(ids, "Approved")       Won/Lost need approve_bid, never your own
    repo.archive(ids) / repo.restore(ids)  repo.delete_draft(id)  (admin, drafts only)
    repo.tracker_shots(code)               repo.tracking(bid)  repo.create_shots(bid_id)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field, replace
from datetime import datetime
from decimal import Decimal
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from slate.core.domain import bidding as DB

from .db_results import DatabaseUnavailableError, DatabaseWriteError

logger = logging.getLogger(__name__)


# ------------------------------------------------------------------ studio settings

def _check_bidding_settings(value):
    """The 'bidding' studio setting: refuse figures that would price nonsense."""
    if not isinstance(value, dict):
        raise ValueError("The bidding figures are a table.")
    out = dict(value)
    if "multipliers" in out:
        table = {}
        for name, days in dict(out["multipliers"] or {}).items():
            name = " ".join(str(name).split()).title()
            if not name:
                continue
            number = float(days)
            if number <= 0:
                raise ValueError(f"'{name}' needs more than zero days per shot.")
            table[name] = number
        if not table:
            raise ValueError("Keep at least one complexity.")
        out["multipliers"] = table
    if "day_rate" in out and out["day_rate"] not in (None, ""):
        if float(out["day_rate"]) <= 0:
            raise ValueError("The day rate must be more than zero.")
    limit = float(out.get("max_margin_percent", DB.DEFAULT_MAX_MARGIN_PERCENT))
    if not 1 <= limit <= 95:
        raise ValueError("The highest margin allowed must be between 1% and 95%.")
    if "margin_percent" in out:
        margin = float(out["margin_percent"])
        if not 0 <= margin <= limit:
            raise ValueError(f"The default margin must be between 0% and {limit:g}%.")
    if "tax_label" in out:
        out["tax_label"] = str(out["tax_label"] or "").strip()[:20]
    return out


try:
    from .studio_settings import register_key
    register_key("bidding", {}, _check_bidding_settings)
except Exception as exc:                    # pragma: no cover - settings module missing
    logger.debug("Bidding settings check not registered: %s", exc)


# ------------------------------------------------------------------ records

@dataclass
class Bid:
    id: Optional[int] = None
    project_code: str = ""
    project_name: str = ""
    client_name: str = ""
    currency: str = "INR"
    day_rate: Decimal = Decimal(0)
    margin: Decimal = Decimal(DB.DEFAULT_MARGIN_PERCENT)
    discount: Decimal = Decimal(0)
    tax: Decimal = Decimal(0)
    tax_label: str = ""
    status: str = DB.DRAFT
    notes: str = ""
    bid_group: Optional[int] = None
    revision: int = 1
    shot_count: int = 0
    complexity: str = ""
    estimated_days: Decimal = Decimal(0)
    estimated_cost: Decimal = Decimal(0)
    estimated_budget: Decimal = Decimal(0)
    tax_amount: Decimal = Decimal(0)
    total_amount: Decimal = Decimal(0)
    created_by: str = ""
    created_at: object = None
    updated_by: str = ""
    updated_at: object = None
    sent_at: object = None
    decided_by: str = ""
    decided_at: object = None
    archived_at: object = None
    archived_by: str = ""
    line_count: int = 0
    has_lines: bool = False

    @classmethod
    def from_row(cls, row) -> "Bid":
        row = dict(row or {})
        bid_id = row.get("id")
        return cls(
            id=int(bid_id) if bid_id is not None else None,
            project_code=str(row.get("project_code") or "").strip(),
            project_name=str(row.get("project_name") or "").strip(),
            client_name=str(row.get("client_name") or "").strip(),
            # Bids made before currencies were recorded were in dollars.
            currency=str(row.get("currency") or "USD").strip().upper(),
            day_rate=DB.dec(row.get("day_rate")),
            margin=DB.dec(row.get("target_margin")),
            discount=DB.dec(row.get("discount_percent")),
            tax=DB.dec(row.get("tax_percent")),
            tax_label=str(row.get("tax_label") or ""),
            status=DB.normalise_status(row.get("status")),
            notes=str(row.get("notes") or ""),
            bid_group=row.get("bid_group") or bid_id,
            revision=int(row.get("revision") or 1),
            shot_count=int(DB.dec(row.get("shot_count"))),
            complexity=str(row.get("complexity") or ""),
            estimated_days=DB.dec(row.get("estimated_days")),
            estimated_cost=DB.money(row.get("estimated_cost")),
            estimated_budget=DB.money(row.get("estimated_budget")),
            tax_amount=DB.money(row.get("tax_amount")),
            total_amount=DB.money(row.get("total_amount") if row.get("total_amount") is not None
                                  else row.get("estimated_budget")),
            created_by=str(row.get("created_by") or ""),
            created_at=row.get("created_at"),
            updated_by=str(row.get("updated_by") or ""),
            updated_at=row.get("updated_at"),
            sent_at=row.get("sent_at"),
            decided_by=str(row.get("decided_by") or ""),
            decided_at=row.get("decided_at"),
            archived_at=row.get("archived_at"),
            archived_by=str(row.get("archived_by") or ""),
            line_count=int(row.get("line_count") or 0),
        )

    @property
    def archived(self) -> bool:
        return bool(self.archived_at)

    @property
    def editable(self) -> bool:
        return DB.is_editable(self.status) and not self.archived

    def as_dict(self) -> dict:
        """The shape bidding.pipeline() reads."""
        return {"id": self.id, "status": self.status, "archived_at": self.archived_at,
                "project_code": self.project_code, "estimated_budget": self.estimated_budget,
                "currency": self.currency, "created_at": self.created_at}

    @property
    def title(self) -> str:
        return f"{self.project_code} v{self.revision}"


def _num(value):
    """sqlite3 cannot bind a Decimal; the text is exact on both backends."""
    return str(value) if isinstance(value, Decimal) else value


class BidRepository:
    def __init__(self, db=None, roles=None, username: str = ""):
        if db is None:
            from .database_manager import database_manager
            db = database_manager
        self.db = db
        self.roles = list(roles) if roles is not None else None
        self.username = username

    # ----------------------------------------------------------- who may
    def _superuser(self) -> bool:
        if self.roles is None:
            return True
        from slate.core.domain import access
        return access.is_superuser(self.roles)

    def can_approve(self) -> bool:
        if self.roles is None:
            return True
        from slate.core.domain import access
        return access.can(self.roles, "approve_bid")

    def decided_refusal(self, bid, by: str = "") -> str:
        """
        Why this person may not revise or archive a won or lost bid, or ''.
        Either one takes a decision out of the list and the pipeline, so it
        needs what deciding needs: approve_bid, and not on your own bid.
        """
        if bid is None or bid.status not in DB.DECIDED:
            return ""
        refusal = DB.decision_refusal(bid.status, can_approve=self.can_approve(),
                                      superuser=self._superuser(), creator=bid.created_by,
                                      me=by or self.username)
        return f"{bid.title} is {DB.status_label(bid.status).lower()}: {refusal}" if refusal else ""

    def can_create_shots(self) -> bool:
        """Creating shots writes to the dashboard, so it needs dashboard_write."""
        if self.roles is None:
            return True
        from slate.core.domain import access
        return access.can(self.roles, "dashboard_write")

    # ----------------------------------------------------------- reads
    def _last_error(self) -> str:
        try:
            return str(self.db.last_error() or "")
        except Exception:
            return ""

    def list(self, include_archived: bool = False, all_revisions: bool = False) -> List[Bid]:
        """
        Bids, newest first. By default the latest revision of each bid and no
        archived ones. Two queries whatever the number of bids (the line count
        is one grouped query, not one per bid). Raises on a failed read.
        """
        rows = self.db.execute_query("SELECT * FROM prod_bidding", fetch="all")
        if rows is None:
            raise RuntimeError(self._last_error() or "the bids could not be read")
        counts = {}
        for r in self.db.execute_query(
                "SELECT bid_id, COUNT(*) AS n FROM prod_bid_lines GROUP BY bid_id", fetch="all") or []:
            r = dict(r)
            counts[r["bid_id"]] = int(r["n"])
        bids = []
        for row in rows:
            bid = Bid.from_row(row)
            bid.line_count = counts.get(bid.id, 0)
            bid.has_lines = bid.line_count > 0
            bids.append(bid)
        if not include_archived:
            bids = [b for b in bids if not b.archived]
        if not all_revisions:
            bids = [b for b in bids if b.status != DB.SUPERSEDED]
        bids.sort(key=lambda b: (str(b.created_at or ""), b.id or 0), reverse=True)
        return bids

    def get(self, bid_id) -> Optional[Bid]:
        row = self.db.execute_query("SELECT * FROM prod_bidding WHERE id = %s", (int(bid_id),),
                                    fetch="one")
        if not row:
            return None
        bid = Bid.from_row(row)
        n = self.db.execute_query("SELECT COUNT(*) AS n FROM prod_bid_lines WHERE bid_id = %s",
                                  (bid.id,), fetch="one")
        bid.line_count = int(dict(n or {}).get("n") or 0)
        bid.has_lines = bid.line_count > 0
        return bid

    def lines(self, bid_id) -> List[DB.BidLine]:
        """The bid's lines; a bid from before line items comes back as one line."""
        rows = self.db.execute_query(
            "SELECT * FROM prod_bid_lines WHERE bid_id = %s ORDER BY position, id", (int(bid_id),),
            fetch="all")
        if rows is None:
            raise RuntimeError(self._last_error() or "the bid lines could not be read")
        if rows:
            return [DB.BidLine.from_row(r) for r in rows]
        header = self.db.execute_query("SELECT * FROM prod_bidding WHERE id = %s", (int(bid_id),),
                                       fetch="one")
        return [DB.legacy_line(dict(header))] if header else []

    def revisions(self, bid_group) -> List[Bid]:
        rows = self.db.execute_query(
            "SELECT * FROM prod_bidding WHERE bid_group = %s OR id = %s ORDER BY revision",
            (int(bid_group), int(bid_group)), fetch="all") or []
        return [Bid.from_row(r) for r in rows]

    def projects(self, active_only: bool = True) -> List[Tuple[str, str]]:
        sql = "SELECT code, name FROM tracking_projects"
        if active_only:
            sql += " WHERE active = 1"
        rows = self.db.execute_query(sql + " ORDER BY code", fetch="all")
        if rows is None:
            raise RuntimeError(self._last_error() or "the projects could not be read")
        return [(str(dict(r)["code"]), str(dict(r).get("name") or "")) for r in rows
                if dict(r).get("code")]

    def tracker_shots(self, project_code: str) -> List[Tuple[str, str, str]]:
        """(reel, shot, status) of the project's shots on the dashboard."""
        rows = self.db.execute_query(
            "SELECT reel, shot_name, status FROM tracking_shots WHERE project_code = %s "
            "ORDER BY reel, shot_name", (project_code,), fetch="all") or []
        return [(str(dict(r).get("reel") or ""), str(dict(r).get("shot_name") or ""),
                 str(dict(r).get("status") or "")) for r in rows]

    def importable_shots(self, project_code: str) -> Tuple[List[Tuple[str, str]], int]:
        """Shots to prefill a bid with: omitted/cancelled ones left out (and counted)."""
        shots, skipped = [], 0
        for reel, shot, status in self.tracker_shots(project_code):
            if status.strip().upper() in DB.OMITTED_SHOT_STATUSES:
                skipped += 1
                continue
            shots.append((reel, shot))
        return shots, skipped

    # ----------------------------------------------------------- writes
    @staticmethod
    def _now():
        return datetime.now().replace(microsecond=0)

    def _priced(self, bid: Bid, lines: Sequence[DB.BidLine]) -> DB.BidTotals:
        problems = DB.check_bid(bid.project_code, lines, bid.margin, bid.discount, bid.tax)
        if problems:
            raise DB.BidError(problems[0])
        return DB.price_bid(lines, bid.margin, bid.discount, bid.tax)

    @staticmethod
    def _complexity(lines: Sequence[DB.BidLine]) -> str:
        kinds = {line.complexity for line in lines if line.complexity}
        return kinds.pop() if len(kinds) == 1 else ("Mixed" if kinds else "")

    def _header_values(self, bid: Bid, lines, totals: DB.BidTotals) -> dict:
        return {
            "project_code": bid.project_code,
            "project_name": bid.project_name or bid.project_code,
            "client_name": bid.client_name or None,
            "currency": bid.currency,
            "day_rate": _num(DB.money(bid.day_rate)),
            "target_margin": _num(DB.dec(bid.margin)),
            "discount_percent": _num(DB.dec(bid.discount)),
            "tax_percent": _num(DB.dec(bid.tax)),
            "tax_label": bid.tax_label or DB.tax_label(bid.currency),
            "notes": bid.notes or None,
            "shot_count": totals.shots,
            "complexity": self._complexity(lines),
            "estimated_days": _num(totals.days),
            "estimated_cost": _num(totals.cost),
            "estimated_budget": _num(totals.taxable),
            "tax_amount": _num(totals.tax_amount),
            "total_amount": _num(totals.total),
        }

    @staticmethod
    def _write_lines(tx, bid_id: int, lines: Sequence[DB.BidLine]) -> None:
        tx.write("DELETE FROM prod_bid_lines WHERE bid_id = %s", (bid_id,))
        for position, line in enumerate(lines):
            tx.write(
                "INSERT INTO prod_bid_lines (bid_id, position, label, shot_name, reel, department, "
                "complexity, shot_count, days_per_shot, day_rate, days, cost, notes) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (bid_id, position, line.label.strip(), line.shot_name.strip(), line.reel.strip(),
                 line.department, line.complexity, int(line.shot_count or 0),
                 _num(DB.dec(line.days_per_shot)), _num(DB.money(line.day_rate)),
                 _num(line.days), _num(line.cost), line.notes or ""))

    def _project_name(self, code: str) -> str:
        row = self.db.execute_query("SELECT name FROM tracking_projects WHERE code = %s", (code,),
                                    fetch="one")
        return str(dict(row).get("name") or "").strip() if row else ""

    def create(self, bid: Bid, lines: Sequence[DB.BidLine], by: str = "",
               revision_of: Optional[Bid] = None) -> int:
        """A new draft with its lines, in one transaction. Returns its id."""
        from .transaction import atomic
        by = by or self.username
        totals = self._priced(bid, lines)
        if not bid.project_name:
            bid.project_name = self._project_name(bid.project_code)
        values = self._header_values(bid, lines, totals)
        now = self._now()
        values.update(status=DB.DRAFT, created_by=by or None, created_at=now,
                      updated_by=by or None, updated_at=now,
                      revision=(revision_of.revision + 1) if revision_of else 1)
        columns = list(values)
        with atomic(self.db) as tx:
            result = tx.write(
                "INSERT INTO prod_bidding (%s) VALUES (%s) RETURNING id"
                % (", ".join(columns), ", ".join(["%s"] * len(columns))),
                tuple(values[c] for c in columns))
            new_id = int(result.last_id)
            group = revision_of.bid_group if revision_of else new_id
            tx.write("UPDATE prod_bidding SET bid_group = %s WHERE id = %s", (group, new_id))
            self._write_lines(tx, new_id, lines)
            if revision_of is not None:
                tx.write("UPDATE prod_bidding SET status = %s, updated_by = %s, updated_at = %s "
                         "WHERE id = %s", (DB.SUPERSEDED, by or None, now, revision_of.id))
        self._log(bid.project_code, new_id, by, "CREATE", "bid", "",
                  f"v{values['revision']} {values['estimated_budget']} {bid.currency}")
        if revision_of is not None:
            self._log(bid.project_code, revision_of.id, by, "UPDATE", "status",
                      revision_of.status, DB.SUPERSEDED)
        return new_id

    def update(self, bid: Bid, lines: Sequence[DB.BidLine], by: str = "") -> None:
        """
        Save a draft's header and lines together. A sent or decided bid is not
        changed in place - it gets a new revision (revise()) - and the project
        never changes here (Duplicate to project makes a new draft instead).
        """
        from .transaction import atomic
        by = by or self.username
        current = self.get(bid.id)
        if current is None:
            raise DB.BidError("That bid no longer exists.")
        if not current.editable:
            raise DB.BidError(f"{current.title} is {DB.status_label(current.status).lower()}"
                              + (" and archived" if current.archived else "")
                              + " - make a new revision to change it.")
        bid.project_code = current.project_code        # locked in edit
        bid.project_name = current.project_name
        totals = self._priced(bid, lines)
        values = self._header_values(bid, lines, totals)
        values.update(updated_by=by or None, updated_at=self._now())
        columns = list(values)
        with atomic(self.db) as tx:
            tx.write("UPDATE prod_bidding SET %s WHERE id = %s"
                     % (", ".join(f"{c} = %s" for c in columns), "%s"),
                     tuple(values[c] for c in columns) + (bid.id,), expect_rows=True)
            self._write_lines(tx, bid.id, lines)
        for attr, column, numeric in (("client_name", "client_name", False),
                                      ("currency", "currency", False),
                                      ("margin", "target_margin", True),
                                      ("discount", "discount_percent", True),
                                      ("tax", "tax_percent", True), ("day_rate", "day_rate", True)):
            old, new = getattr(current, attr), getattr(bid, attr)
            differs = DB.dec(old) != DB.dec(new) if numeric else str(old or "") != str(new or "")
            if differs:
                self._log(current.project_code, bid.id, by, "UPDATE", column, old, new)
        if current.estimated_budget != DB.money(totals.taxable):
            self._log(current.project_code, bid.id, by, "UPDATE", "estimated_budget",
                      current.estimated_budget, totals.taxable)

    def revise(self, bid_id, by: str = "") -> int:
        """
        A new draft revision copied from this one (header and lines); this one
        becomes Superseded and stays, read-only. Only the latest revision can be
        revised.
        """
        current = self.get(bid_id)
        if current is None:
            raise DB.BidError("That bid no longer exists.")
        if current.status == DB.SUPERSEDED:
            raise DB.BidError(f"{current.title} already has a newer revision.")
        refusal = self.decided_refusal(current, by)
        if refusal:
            raise PermissionError(refusal)
        lines = self.lines(bid_id)
        copy = replace(current, id=None, status=DB.DRAFT)
        return self.create(copy, [replace(l, id=None) for l in lines], by=by, revision_of=current)

    def duplicate_to_project(self, bid_id, project_code: str, by: str = "") -> int:
        """A new draft (revision 1) for another project, with the same lines."""
        current = self.get(bid_id)
        if current is None:
            raise DB.BidError("That bid no longer exists.")
        copy = replace(current, id=None, status=DB.DRAFT, project_code=project_code,
                       project_name="", bid_group=None, revision=1)
        return self.create(copy, [replace(l, id=None) for l in self.lines(bid_id)], by=by)

    def set_status(self, ids: Sequence[int], status: str, by: str = "") -> Dict[int, str]:
        """
        Move bids to a status, all or nothing. Won/Lost - and reopening a bid
        that was decided - need the approve_bid ability and are refused on a
        bid you made yourself (unless you are Admin/Developer). Records sent_at
        or decided_by/decided_at. Returns {id: previous status}.
        """
        from .transaction import atomic
        by = by or self.username
        status = DB.normalise_status(status)
        bids = [self.get(i) for i in ids]
        previous = {}
        for bid in bids:
            if bid is None:
                raise DB.BidError("One of those bids no longer exists.")
            if bid.archived:
                raise DB.BidError(f"{bid.title} is archived - restore it first.")
            if bid.status == status:
                continue
            if not DB.can_change(bid.status, status):
                raise DB.BidError(f"{bid.title} cannot go from {DB.status_label(bid.status)} to "
                                  f"{DB.status_label(status)}.")
            if status in DB.DECIDED or bid.status in DB.DECIDED:
                refusal = DB.decision_refusal(status, can_approve=self.can_approve(),
                                              superuser=self._superuser(),
                                              creator=bid.created_by, me=by)
                if refusal:
                    raise PermissionError(f"{bid.title}: {refusal}")
        now = self._now()
        with atomic(self.db) as tx:
            for bid in bids:
                previous[bid.id] = bid.status
                if bid.status == status:
                    continue
                sets = ["status = %s", "updated_by = %s", "updated_at = %s"]
                params = [status, by or None, now]
                if status == DB.SENT:
                    sets.append("sent_at = %s")
                    params.append(now)
                if status in DB.DECIDED:
                    sets += ["decided_by = %s", "decided_at = %s"]
                    params += [by or None, now]
                elif bid.status in DB.DECIDED:
                    sets += ["decided_by = %s", "decided_at = %s"]
                    params += [None, None]
                tx.write("UPDATE prod_bidding SET %s WHERE id = %%s" % ", ".join(sets),
                         tuple(params) + (bid.id,), expect_rows=True)
        for bid in bids:
            if bid.status != status:
                self._log(bid.project_code, bid.id, by, "UPDATE", "status", bid.status, status)
        return previous

    def archive(self, ids: Sequence[int], by: str = "") -> int:
        """Hide bids (kept, restorable, out of the pipeline). All or nothing."""
        from .transaction import atomic
        by = by or self.username
        now = self._now()
        bids = [b for b in (self.get(i) for i in ids) if b is not None]
        for bid in bids:
            refusal = self.decided_refusal(bid, by)
            if refusal:
                raise PermissionError(refusal)
        with atomic(self.db) as tx:
            for bid in bids:
                tx.write("UPDATE prod_bidding SET archived_at = %s, archived_by = %s WHERE id = %s",
                         (now, by or None, bid.id), expect_rows=True)
        for bid in bids:
            self._log(bid.project_code, bid.id, by, "ARCHIVE", "archived", "", "yes")
        return len(bids)

    def restore(self, ids: Sequence[int], by: str = "") -> int:
        from .transaction import atomic
        by = by or self.username
        bids = [b for b in (self.get(i) for i in ids) if b is not None]
        for bid in bids:
            refusal = self.decided_refusal(bid, by)
            if refusal:
                raise PermissionError(refusal)
        with atomic(self.db) as tx:
            for bid in bids:
                tx.write("UPDATE prod_bidding SET archived_at = NULL, archived_by = NULL WHERE id = %s",
                         (bid.id,), expect_rows=True)
        for bid in bids:
            self._log(bid.project_code, bid.id, by, "RESTORE", "archived", "yes", "")
        return len(bids)

    def delete_draft(self, bid_id, by: str = "") -> None:
        """
        Delete a bid for good: Admin/Developer only, and only a draft that has
        no other revisions - everything else is archived, so its history stays.
        """
        from .transaction import atomic
        if not self._superuser():
            raise PermissionError("Only Admin or Developer can delete a bid; archive it instead.")
        bid = self.get(bid_id)
        if bid is None:
            return
        if bid.status != DB.DRAFT or len(self.revisions(bid.bid_group or bid.id)) > 1:
            raise DB.BidError(f"{bid.title} has history (it was sent, decided or revised) - "
                              "archive it instead.")
        with atomic(self.db) as tx:
            tx.write("DELETE FROM prod_bid_lines WHERE bid_id = %s", (bid.id,))
            tx.write("DELETE FROM prod_bidding WHERE id = %s", (bid.id,), expect_rows=True)
        self._log(bid.project_code, bid.id, by or self.username, "DELETE", "bid", bid.title, "")

    # ----------------------------------------------------------- tracking
    def tasks(self, project_code: str) -> List[dict]:
        """The dashboard's tasks for a project (department, status, bid days, actual days)."""
        from .migrations.workplace_schema import _column_exists
        actual = "t.actual_days" if _column_exists(self.db, "tracking_tasks", "actual_days") \
            else "NULL AS actual_days"
        rows = self.db.execute_query(
            "SELECT t.department, t.status, t.bid_days, %s, s.shot_name, s.reel "
            "FROM tracking_tasks t JOIN tracking_shots s ON s.id = t.shot_id "
            "WHERE s.project_code = %%s" % actual, (project_code,), fetch="all")
        if rows is None:
            raise RuntimeError(self._last_error() or "the dashboard tasks could not be read")
        return [dict(r) for r in rows]

    def tracking(self, bid: Bid) -> DB.Tracking:
        return DB.track(self.lines(bid.id), self.tasks(bid.project_code),
                        self.tracker_shots(bid.project_code), default_rate=bid.day_rate)

    def create_shots(self, bid_id, by: str = "") -> dict:
        """
        Register the shots a won bid names on the dashboard, through the shot
        registry (the same path an ingest uses), with each department's bid
        days on the new shots' tasks. Shots already on the dashboard are left
        exactly as they are. Returns {'created': [...], 'existing': [...],
        'group_lines': n, 'error': ''}.
        """
        if not self.can_create_shots():
            raise PermissionError("Creating shots on the dashboard needs the right to edit the "
                                  "dashboard (dashboard_write).")
        bid = self.get(bid_id)
        if bid is None:
            raise DB.BidError("That bid no longer exists.")
        if bid.status != DB.WON:
            raise DB.BidError("Shots are created from a won bid.")
        lines = self.lines(bid_id)
        wanted = DB.shots_to_create(lines)
        group_lines = sum(1 for l in lines if not l.shot_name.strip())
        out = {"created": [], "existing": [], "group_lines": group_lines, "error": ""}
        if not wanted:
            return out
        from slate.core.domain.shot_registry import register_ingested_shots
        result = register_ingested_shots(
            bid.project_code, [{"reel": reel, "shot": shot} for (reel, shot) in wanted],
            project_name=bid.project_name, db=self.db)
        if getattr(result, "error", ""):
            out["error"] = result.error
            return out
        created = set(result.created)
        out["created"] = list(result.created)
        out["existing"] = list(result.already_present)
        out["refused"] = list(getattr(result, "refused", []) or [])
        if created:
            ids = {}
            for row in self.db.get_tracking_shots(bid.project_code) or []:
                ids[(str(row.get("reel_episode") or row.get("reel") or "").casefold(),
                     str(row.get("shot_name") or "").casefold())] = row.get("id")
            tasks = []
            for (reel, shot), depts in wanted.items():
                if shot not in created:
                    continue
                shot_id = ids.get((reel.casefold(), shot.casefold()))
                if shot_id is None:
                    continue
                for dept, days in depts.items():
                    tasks.append({"shot_id": shot_id, "department": dept, "status": "",
                                  "artist": "", "artist_id": None, "bid_days": float(days),
                                  "target": ""})
            if tasks and not self.db.save_tracking_tasks(bid.project_code, tasks):
                out["error"] = "the shots were created but their bid days could not be saved"
        self._log(bid.project_code, bid.id, by or self.username, "CREATE SHOTS", "shots", "",
                  f"{len(out['created'])} created")
        return out

    # ----------------------------------------------------------- history
    def _log(self, project, bid_id, by, action, field, old, new) -> None:
        if not by:
            return
        try:
            self.db.log_change_event(project or "", "bid", str(bid_id), by, action, field,
                                     "" if old is None else str(old), "" if new is None else str(new))
        except DatabaseUnavailableError:
            raise
        except Exception as exc:
            logger.warning("Bid change not written to the history: %s", exc)
