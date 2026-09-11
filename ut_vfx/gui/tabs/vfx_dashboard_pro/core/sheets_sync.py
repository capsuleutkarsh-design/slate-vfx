import logging
import os
from pathlib import Path
from typing import Dict, Iterable, List, Optional

import gspread
from oauth2client.service_account import ServiceAccountCredentials

from ut_vfx.core.infra.global_config import GlobalConfig

from ..models.shot_model import Shot


class GoogleSheetsSync:
    """
    Optional cloud sync helper.
    Uses a stable 16-column schema and maps to current Shot model fields.
    """

    HEADERS = [
        "Index",
        "Reel",
        "Shot",
        "Description",
        "Frames",
        "EXR Frames",
        "Reference",
        "Shot Type",
        "Scan Status",
        "Priority",
        "Master Status",
        "Comp Status",
        "Roto Status",
        "Notes",
        "Edit Status",
        "Target Date",
    ]

    def __init__(self, credentials_file: str = ""):
        self.credentials_file = str(credentials_file or "").strip()
        self.resolved_credentials_file = ""
        self.last_error = ""
        self.client = None
        self.sheet = None

    def connect(self, sheet_url: str) -> bool:
        self.last_error = ""
        self.resolved_credentials_file = ""

        creds_path = self._discover_credentials_file()
        if not creds_path:
            self.last_error = (
                "Google service account credentials not found. "
                "Set UTVFX_GOOGLE_CREDENTIALS or configure "
                "'google_sheets_credentials' in client config."
            )
            logging.warning("GoogleSheetsSync disabled: %s", self.last_error)
            return False

        try:
            scope = [
                "https://spreadsheets.google.com/feeds",
                "https://www.googleapis.com/auth/drive",
            ]
            creds = ServiceAccountCredentials.from_json_keyfile_name(creds_path, scope)
            self.client = gspread.authorize(creds)
            self.sheet = self.client.open_by_url(sheet_url).sheet1
            self.resolved_credentials_file = creds_path
            return True
        except Exception as exc:
            self.last_error = f"Google Sheets connection error: {exc}"
            logging.exception(self.last_error)
            return False

    def _discover_credentials_file(self) -> str:
        for candidate in self._credential_candidates():
            if candidate and candidate.exists() and candidate.is_file():
                return str(candidate)
        return ""

    def _credential_candidates(self) -> Iterable[Path]:
        configured = [
            self.credentials_file,
            os.getenv("UTVFX_GOOGLE_CREDENTIALS", ""),
            os.getenv("GOOGLE_APPLICATION_CREDENTIALS", ""),
            GlobalConfig.get("google_sheets_credentials", ""),
            GlobalConfig.get("google_service_account_json", ""),
        ]
        for value in configured:
            candidate = self._normalize_path(value)
            if candidate:
                yield candidate

        # Common fallback locations for studio and local installs.
        dashboard_root = Path(__file__).resolve().parent.parent
        fallback_paths = [
            dashboard_root / "config" / "google_service_account.json",
            dashboard_root / "config" / "credentials.json",
            Path.cwd() / "credentials.json",
            GlobalConfig.server_root() / "Config" / "google_service_account.json",
            GlobalConfig.server_root() / "Config" / "credentials.json",
        ]
        localapp = os.getenv("LOCALAPPDATA", "")
        if localapp:
            fallback_paths.append(Path(localapp) / "UTVFX" / "credentials" / "google_service_account.json")
            fallback_paths.append(Path(localapp) / "UTVFX" / "credentials" / "credentials.json")

        for candidate in fallback_paths:
            yield candidate

    @staticmethod
    def _normalize_path(raw: object) -> Optional[Path]:
        text = str(raw or "").strip()
        if not text:
            return None
        expanded = os.path.expandvars(os.path.expanduser(text))
        if "$SERVER" in expanded:
            expanded = GlobalConfig.resolve_path(expanded)
        try:
            return Path(expanded)
        except Exception:
            return None

    @staticmethod
    def _shot_to_row(index: int, shot: Shot) -> List[str]:
        frames = str(int(shot.edit_frames)) if shot.edit_frames else ""
        return [
            str(index),
            shot.reel_episode or "",
            shot.shot_name or "",
            shot.description or shot.sow or "",
            frames,
            frames,  # fallback: no separate EXR frame field in Shot
            shot.thumbnail_path or "",
            shot.shot_type or "",
            shot.scan_status or "",
            str(shot.priority or 0),
            shot.status or "",
            shot.comp_dept.status or "",
            shot.roto_dept.status or "",
            shot.notes or "",
            shot.edit_status or "",
            shot.target or "",
        ]

    @staticmethod
    def _row_to_shot(row: List[str]) -> Shot:
        row = row + [""] * (16 - len(row))
        shot = Shot(
            reel_episode=row[1],
            shot_name=row[2],
            description=row[3],
            shot_type=row[7],
            scan_status=row[8],
            status=row[10] or "WIP",
            notes=row[13],
            edit_status=row[14],
            target=row[15],
            thumbnail_path=row[6],
        )

        try:
            shot.edit_frames = float(row[4]) if row[4] else 0.0
        except (TypeError, ValueError):
            shot.edit_frames = 0.0

        try:
            shot.priority = int(row[9]) if row[9] else 0
        except (TypeError, ValueError):
            shot.priority = 0

        shot.comp_dept.status = row[11] or ""
        shot.roto_dept.status = row[12] or ""
        return shot

    def upload_shots(self, shots: List[Shot]):
        if not self.sheet:
            return False

        try:
            self.sheet.clear()
            self.sheet.append_row(self.HEADERS)
            rows = [self._shot_to_row(i + 1, shot) for i, shot in enumerate(shots)]
            if rows:
                self.sheet.append_rows(rows)
            return True
        except Exception as e:
            logging.exception(f"Upload error: {e}")
            return False

    def download_shots(self) -> List[Shot]:
        if not self.sheet:
            return []

        try:
            all_values = self.sheet.get_all_values()
        except Exception as e:
            logging.exception(f"Download error: {e}")
            return []

        if not all_values:
            return []

        shots = []
        for row in all_values[1:]:
            shot = self._row_to_shot(row)
            if shot.shot_name:
                shots.append(shot)
        return shots

    def find_conflicts(self, local_shots: List[Shot], cloud_shots: List[Shot]) -> List[Dict]:
        """
        Basic conflict detection by shot_name and key editable fields.
        """
        conflicts = []
        cloud_map = {s.shot_name: s for s in cloud_shots if s.shot_name}

        for local in local_shots:
            if not local.shot_name:
                continue
            cloud = cloud_map.get(local.shot_name)
            if not cloud:
                continue

            if (
                (local.status or "") != (cloud.status or "")
                or (local.description or local.sow or "") != (cloud.description or cloud.sow or "")
                or (local.notes or "") != (cloud.notes or "")
            ):
                conflicts.append(
                    {
                        "shot_id": local.shot_name,
                        "local": local,
                        "cloud": cloud,
                    }
                )

        return conflicts
