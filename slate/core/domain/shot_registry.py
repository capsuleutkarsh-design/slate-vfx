"""
Turning a scan delivery into dashboard shots.

Before this, the two halves of the software never spoke: Build & Ingest built
the folders and moved the plates, and the dashboard got its shots from an Excel
import. That made the spreadsheet load-bearing.

The ingest already works out every reel and shot on the client drive. This
module writes those out as tracking records, so a delivery lands in the
dashboard on its own.

The one rule that matters: **an existing shot is never touched.** Re-ingesting
a drive, or a client re-delivering a shot, must not reset an artist assignment,
a status or a bid. Only genuinely new shots are created.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import date
from typing import Dict, Iterable, List, Optional

from slate.core.domain.departments import load_departments


# Status given to a shot that has just arrived and has no work booked yet.
NEW_SHOT_STATUS = "YTS"


@dataclass
class IngestedShot:
    """One shot found on a client drive."""
    reel: str
    shot: str
    path: str = ""
    scan_version: str = ""
    source_folder: str = ""
    first_frame: int = 0
    last_frame: int = 0


@dataclass
class RegistrationResult:
    created: List[str] = field(default_factory=list)
    already_present: List[str] = field(default_factory=list)
    new_scans: List[str] = field(default_factory=list)
    project_created: bool = False
    error: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.error is None

    def summary(self) -> str:
        if self.error:
            return f"Dashboard update failed: {self.error}"
        parts = [f"{len(self.created)} new shot(s) added to the dashboard"]
        if self.new_scans:
            parts.append(f"{len(self.new_scans)} existing shot(s) flagged with a new scan")
        if self.already_present:
            parts.append(f"{len(self.already_present)} already tracked")
        return " | ".join(parts)


def _department_folder_template() -> Dict[str, str]:
    """Per-department folder patterns, taken from the department registry."""
    template = {
        "scan": "05_Reels/{reel}/{shot}/01_Scan",
        "deliver": "05_Reels/{reel}/{shot}/08_Deliver",
        "annotation": "05_Reels/{reel}/{shot}/00_Annotation",
    }
    for dept in load_departments():
        if dept.folder:
            template[dept.key] = "05_Reels/{reel}/{shot}/" + dept.folder
    return template


def _shot_folder_paths(reel: str, shot: str) -> Dict[str, str]:
    """Resolve the folder template for one shot."""
    return {
        key: pattern.format(reel=reel, shot=shot)
        for key, pattern in _department_folder_template().items()
    }


def _ensure_project(db, project_code: str, project_name: str,
                    folder_base: str = "") -> bool:
    """
    Make sure the dashboard knows about this project.

    Returns True when a project row was created. An existing project's
    configuration is left alone.
    """
    try:
        existing = db.get_tracking_project(project_code)
    except Exception:
        existing = None

    if existing:
        return False

    config = {
        "code": project_code,
        "name": project_name or project_code,
        "project_number": 0,
        "excel_path": "",
        "folder_base": str(folder_base or ""),
        "folder_template": _department_folder_template(),
        "created_by": "build_and_ingest",
    }
    saved = db.save_tracking_project(project_code, project_name or project_code,
                                     json.dumps(config))
    if saved is False:
        # A project that was not written means shots with nowhere to appear.
        # Better to say so than to leave a coordinator hunting for it.
        raise RuntimeError(
            f"The project '{project_code}' could not be created in the database."
        )
    logging.info("Created dashboard project '%s' from ingest", project_code)
    return True


def _scan_status_text(version: str) -> str:
    """What the dashboard's Scan Status column shows after a delivery."""
    return f"{version} received {date.today().isoformat()}"


def _flag_new_scan(db, project_code: str, entry: "IngestedShot",
                   existing_rows: List[dict]) -> bool:
    """
    Mark an already-tracked shot as having received another scan.

    Only the scan status and an internal note are touched - never the status,
    the artist, the bid or anything else a coordinator has set.
    """
    from slate.gui.tabs.vfx_dashboard_pro.models.shot_model import Shot
    from slate.gui.tabs.vfx_dashboard_pro.models.shot_model import FeedbackEntry

    row = next(
        (r for r in existing_rows
         if str(r.get("shot_name", "")).strip().lower() == entry.shot.strip().lower()
         and str(r.get("reel_episode", "") or "").strip().lower()
             == entry.reel.strip().lower()),
        None,
    )
    if not row:
        return False

    try:
        shot = Shot.from_dict(row)
        shot.shot_name = row.get("shot_name") or entry.shot
        shot.scan_status = _scan_status_text(entry.scan_version)

        # A re-delivered plate can be a different length from the last one.
        if entry.last_frame:
            shot.first_frame = entry.first_frame
            shot.last_frame = entry.last_frame

        detail = f"New scan {entry.scan_version} ingested"
        if entry.source_folder and entry.source_folder != entry.shot:
            detail += f" (from {entry.source_folder})"
        shot.feedback_internal.append(FeedbackEntry(
            date=date.today().isoformat(),
            source="ingest",
            text=detail,
            logged_by="Build & Ingest",
        ))

        db.save_tracking_shots(project_code, [(
            shot.shot_name, shot.status, shot.priority,
            json.dumps(shot.to_dict(), default=str),
        )])

        _notify_artists(shot, entry)
        return True
    except Exception as exc:
        logging.warning("Could not flag new scan on %s: %s", entry.shot, exc)
        return False


def _notify_artists(shot, entry: "IngestedShot") -> None:
    """Tell whoever is on the shot that a new scan landed."""
    try:
        from slate.core.domain.notification_manager import NotificationManager
        notifier = NotificationManager()
    except Exception:
        return

    message = f"New scan {entry.scan_version} arrived for {shot.shot_name}"
    for artist in set(shot.get_all_artists()):
        if not artist:
            continue
        try:
            notifier.add_notification(artist, message, "scan")
        except Exception as exc:
            logging.debug("Scan notification failed for %s: %s", artist, exc)


def register_ingested_shots(
    project_code: str,
    shots: Iterable,
    project_name: str = "",
    folder_base: str = "",
    db=None,
) -> RegistrationResult:
    """
    Create dashboard records for shots found during an ingest.

    `shots` may be IngestedShot instances or plain dicts with 'reel'/'shot'
    keys, which is what FolderCreationWorker produces.
    """
    result = RegistrationResult()

    project_code = str(project_code or "").strip()
    if not project_code:
        result.error = "no project code"
        return result

    entries: List[IngestedShot] = []
    for raw in shots or []:
        if isinstance(raw, IngestedShot):
            entry = raw
        elif isinstance(raw, dict):
            entry = IngestedShot(
                reel=str(raw.get("reel", "") or ""),
                shot=str(raw.get("shot", "") or ""),
                path=str(raw.get("path", "") or ""),
                scan_version=str(raw.get("scan_version", "") or ""),
                source_folder=str(raw.get("source_folder", "") or ""),
                first_frame=int(raw.get("first_frame") or 0),
                last_frame=int(raw.get("last_frame") or 0),
            )
        else:
            continue
        if entry.shot:
            entries.append(entry)

    if not entries:
        return result

    if db is None:
        from slate.core.infra.database_manager import database_manager
        db = database_manager

    try:
        result.project_created = _ensure_project(
            db, project_code, project_name, folder_base
        )

        existing_rows = db.get_tracking_shots(project_code) or []
        # A shot is identified by its reel and its name together: SH010 in
        # ReelA and SH010 in ReelB are different shots.
        existing_names = {
            (str(row.get("reel_episode", "") or "").strip().lower(),
             str(row.get("shot_name", "")).strip().lower())
            for row in existing_rows
        }

        # Deferred import: the dashboard model pulls in Qt-free code only, but
        # keeping it lazy avoids a hard dependency for non-dashboard callers.
        from slate.gui.tabs.vfx_dashboard_pro.models.shot_model import Shot

        to_create = []
        seen = set()
        for entry in entries:
            if not entry.shot.strip():
                continue
            key = (entry.reel.strip().lower(), entry.shot.strip().lower())
            if key in seen:
                continue
            seen.add(key)

            if key in existing_names:
                result.already_present.append(entry.shot)
                # A shot already tracked has just had another scan delivered.
                # Flag it so nobody has to notice by looking at the drive.
                if entry.scan_version and _flag_new_scan(
                        db, project_code, entry, existing_rows):
                    result.new_scans.append(entry.shot)
                continue

            shot = Shot(
                shot_name=entry.shot,
                reel_episode=entry.reel,
                status=NEW_SHOT_STATUS,
                scan_status=_scan_status_text(entry.scan_version or "v001"),
                folder_paths=_shot_folder_paths(entry.reel, entry.shot),
                first_frame=entry.first_frame,
                last_frame=entry.last_frame,
            )
            to_create.append(
                (shot.shot_name, shot.status, shot.priority,
                 json.dumps(shot.to_dict(), default=str))
            )
            result.created.append(entry.shot)

        if to_create:
            db.save_tracking_shots(project_code, to_create)
            logging.info(
                "Ingest added %d shot(s) to dashboard project '%s'",
                len(to_create), project_code
            )

    except Exception as exc:
        logging.exception("Could not register ingested shots: %s", exc)
        result.error = str(exc)
        result.created = []

    return result
