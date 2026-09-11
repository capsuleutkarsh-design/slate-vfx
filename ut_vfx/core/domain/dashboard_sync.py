"""
Dashboard Sync Module

Synchronizes shot review data with VFX Dashboard database.
Pulls shots, project info, notes, and artist assignments.
Pushes review statuses and notes back to dashboard.
"""

import json
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
import asyncio

from ...core.infra.database_manager import DatabaseManager
from ...core.infra.global_config import GlobalConfig
from .review_shot import ReviewShot, ShotStatus

logger = logging.getLogger(__name__)


class DashboardSync:
    """Sync shot review data with VFX Dashboard"""
    
    def __init__(self):
        self.db = DatabaseManager()
        self._active_project_code: Optional[str] = None
        self._reel_dir_cache: Dict[tuple, List[Path]] = {}
        self._shot_dir_cache: Dict[tuple, List[Path]] = {}

    def _is_local_fallback_mode(self) -> bool:
        try:
            status = self.db.get_runtime_status() or {}
            active_mode = str(status.get("active_mode", "")).lower()
            return active_mode == "sqlite" and bool(status.get("fallback_used", False))
        except Exception:
            return False

    def _is_sqlite_backend(self) -> bool:
        backend = getattr(self.db, "backend", self.db)
        return "sqlite" in backend.__class__.__name__.lower()

    async def _table_exists(self, table_name: str) -> bool:
        """Check table existence without emitting backend-specific SQL errors."""
        try:
            if self._is_sqlite_backend():
                row = await self.db.execute_query(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name=%s LIMIT 1",
                    (table_name,),
                    fetch="one",
                )
            else:
                row = await self.db.execute_query(
                    """
                    SELECT table_name
                    FROM information_schema.tables
                    WHERE table_schema='public' AND table_name=%s
                    LIMIT 1
                    """,
                    (table_name,),
                    fetch="one",
                )
            return bool(row)
        except Exception:
            return False

    @staticmethod
    def _row_get(row, key: str, index: int, default=None):
        if isinstance(row, dict):
            return row.get(key, default)
        if isinstance(row, (list, tuple)):
            return row[index] if len(row) > index else default
        return default

    @staticmethod
    def _safe_json_load(raw: Any) -> Dict[str, Any]:
        if isinstance(raw, dict):
            return dict(raw)
        if not raw:
            return {}
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}

    @staticmethod
    def _to_notes_list(value: Any) -> List[str]:
        if value is None:
            return []
        if isinstance(value, list):
            out = [str(v).strip() for v in value if str(v).strip()]
            return out
        if isinstance(value, str):
            return [line.strip() for line in value.splitlines() if line.strip()]
        text = str(value).strip()
        return [text] if text else []

    async def _get_tracking_projects(self) -> List[Dict[str, Any]]:
        """
        Read tracking projects from tracking_projects using config_json as source of truth.
        Returns normalized records: code, name, path, folder_template, config.
        """
        if self._is_sqlite_backend():
            active_query = """
                SELECT code, name, config_json, last_updated
                FROM tracking_projects
                WHERE active=1
                ORDER BY last_updated DESC, code
            """
        else:
            active_query = """
                SELECT code, name, config_json, last_updated
                FROM tracking_projects
                WHERE LOWER(COALESCE(active::text, '')) IN ('1', 't', 'true', 'y', 'yes')
                ORDER BY last_updated DESC NULLS LAST, code
            """

        queries = (
            active_query,
            """
                SELECT code, name, config_json, last_updated
                FROM tracking_projects
                ORDER BY last_updated DESC NULLS LAST, code
            """,
        )

        rows = None
        for query in queries:
            try:
                rows = await self.db.execute_query(query) or []
                if rows:
                    break
            except Exception:
                continue

        if not rows:
            return []

        projects: List[Dict[str, Any]] = []
        seen_codes = set()
        for row in rows:
            cfg = self._safe_json_load(self._row_get(row, "config_json", 2, "{}"))
            code = str(self._row_get(row, "code", 0, cfg.get("code", "")) or cfg.get("code", "")).strip()
            name = str(self._row_get(row, "name", 1, cfg.get("name", code)) or cfg.get("name", code)).strip()
            if not code:
                continue
            if code in seen_codes:
                continue
            seen_codes.add(code)

            path = str(
                cfg.get("folder_base")
                or cfg.get("target_directory")
                or cfg.get("path")
                or ""
            ).strip()
            folder_template = cfg.get("folder_template", {})
            if not isinstance(folder_template, dict):
                folder_template = {}

            projects.append(
                {
                    "code": code,
                    "name": name or code,
                    "path": path,
                    "folder_template": folder_template,
                    "config": cfg,
                    "last_updated": self._row_get(row, "last_updated", 3),
                }
            )
        return projects

    async def _resolve_tracking_project(self, project_name: Optional[str] = None) -> Optional[Dict[str, Any]]:
        projects = await self._get_tracking_projects()
        if not projects:
            return None

        if project_name:
            target = str(project_name).strip().lower()
            for project in projects:
                if project["name"].strip().lower() == target or project["code"].strip().lower() == target:
                    self._active_project_code = project["code"]
                    return project

        if self._active_project_code:
            for project in projects:
                if project["code"] == self._active_project_code:
                    return project

        # Most recently updated project is first due ORDER BY.
        project = projects[0]
        self._active_project_code = project["code"]
        return project

    async def _get_active_legacy_project(self) -> Optional[dict]:
        """
        Legacy fallback for older installs that still rely on projects table.
        """
        try:
            if not await self._table_exists("projects"):
                return None
            queries_to_try = (
                "SELECT name, target_directory FROM projects ORDER BY created_at DESC LIMIT 1",
                "SELECT name, target_directory FROM projects ORDER BY name LIMIT 1",
            )
            for query in queries_to_try:
                try:
                    result = await self.db.execute_query(query)
                    if result and len(result) > 0:
                        name = self._row_get(result[0], "name", 0)
                        target = self._row_get(result[0], "target_directory", 1)
                        return {"name": name, "path": target}
                except Exception:
                    continue
            return None
        except Exception as e:
            logger.error(f"Error getting legacy active project: {e}", exc_info=True)
            return None
    
    async def get_active_project(self) -> Optional[dict]:
        """
        Get currently active project from dashboard

        Returns:
            Dict with project info or None
        """
        project = await self._resolve_tracking_project()
        if project:
            return {"code": project["code"], "name": project["name"], "path": project["path"]}
        return await self._get_active_legacy_project()

    def _candidate_dir_from_template(self, folder_base: str, template: str, reel: str, shot_name: str) -> Optional[Path]:
        if not folder_base or not template:
            return None
        try:
            rel = template.format(reel=reel, shot=shot_name)
            rel = rel.replace("\\", "/")
            return Path(folder_base) / Path(rel)
        except Exception:
            return None

    @staticmethod
    def _normalize_key(value: Any) -> str:
        return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())

    @staticmethod
    def _extract_digits(value: Any) -> List[str]:
        return re.findall(r"\d+", str(value or ""))

    @staticmethod
    def _resolve_path_value(path_value: Any) -> Path:
        text = str(path_value or "").strip()
        if not text:
            return Path()
        if "$SERVER" in text:
            text = GlobalConfig.resolve_path(text)
        return Path(text)

    @staticmethod
    def _dedupe_paths(paths: List[Path]) -> List[Path]:
        unique: List[Path] = []
        seen = set()
        for path in paths:
            key = str(path).replace("\\", "/").rstrip("/").lower()
            if not key or key in seen:
                continue
            seen.add(key)
            unique.append(path)
        return unique

    @staticmethod
    def _template_suffix_after_shot(template: str) -> Optional[Path]:
        if not template:
            return None
        tpl = str(template).replace("\\", "/")
        if "{shot}" not in tpl:
            return None
        suffix = tpl.split("{shot}", 1)[1].lstrip("/")
        if not suffix:
            return Path()
        return Path(suffix)

    def _project_root_candidates(self, project: Dict[str, Any]) -> List[Path]:
        candidates: List[Path] = []
        folder_base = str(project.get("path") or "").strip()
        code = str(project.get("code") or "").strip()
        name = str(project.get("name") or "").strip()
        short_code = code.split("_", 1)[-1] if "_" in code else code

        if folder_base:
            base = self._resolve_path_value(folder_base)
            if str(base):
                candidates.append(base)
                if code:
                    candidates.append(base / code)

                # If this looks like a drive root or generic root, search matching project folders.
                if base.exists() and base.is_dir() and not (base / "05_Reels").exists():
                    code_norm = self._normalize_key(code)
                    short_norm = self._normalize_key(short_code)
                    name_norm = self._normalize_key(name)
                    try:
                        for child in base.iterdir():
                            if not child.is_dir():
                                continue
                            child_norm = self._normalize_key(child.name)
                            if (
                                (code_norm and code_norm in child_norm)
                                or (short_norm and short_norm in child_norm)
                                or (name_norm and name_norm in child_norm)
                            ):
                                candidates.append(child)
                    except Exception as exc:
                        logger.debug("Skipping directory candidate %s: %s", child, exc)

        if code:
            candidates.append(Path("X:/") / code)

        return self._dedupe_paths(candidates)

    def _reel_aliases(self, reel: str) -> List[str]:
        reel_text = str(reel or "").strip().replace(" ", "_")
        if not reel_text:
            return []

        aliases = {reel_text}
        digits = self._extract_digits(reel_text)
        if reel_text.upper().startswith("SC_") and digits:
            aliases.add(f"REEL_{digits[0]}")
            aliases.add(f"RL_{digits[0]}")
        if reel_text.upper().startswith("RL_"):
            aliases.add(reel_text.upper().replace("RL_", "REEL_", 1))
        if reel_text.upper().startswith("REEL_"):
            tail = reel_text.split("_", 1)[1] if "_" in reel_text else reel_text
            aliases.add(f"RL_{tail}")
            aliases.add(f"SC_{tail}")

        return list(aliases)

    def _find_reel_dirs(self, root: Path, reel: str) -> List[Path]:
        cache_key = (str(root).replace("\\", "/").lower(), str(reel or "").lower())
        if cache_key in self._reel_dir_cache:
            return self._reel_dir_cache[cache_key]

        reels_root = root / "05_Reels"
        if not reels_root.exists():
            self._reel_dir_cache[cache_key] = []
            return []

        aliases = self._reel_aliases(reel)
        alias_norms = {self._normalize_key(a) for a in aliases if a}
        reel_digits = set(self._extract_digits(reel))
        matches: List[tuple] = []

        try:
            for child in reels_root.iterdir():
                if not child.is_dir():
                    continue
                child_name = child.name
                child_norm = self._normalize_key(child_name)
                score = 0

                if child_name in aliases:
                    score += 220
                if child_norm in alias_norms:
                    score += 200
                if any(a and a.lower() == child_name.lower() for a in aliases):
                    score += 180

                child_digits = set(self._extract_digits(child_name))
                if reel_digits and child_digits and reel_digits.intersection(child_digits):
                    score += 90

                if score > 0:
                    matches.append((score, child))
        except Exception:
            self._reel_dir_cache[cache_key] = []
            return []

        matches.sort(key=lambda row: (row[0], len(row[1].name)), reverse=True)
        result = [row[1] for row in matches]
        self._reel_dir_cache[cache_key] = result
        return result

    def _find_shot_dirs(self, reel_dir: Path, shot_name: str, sequence: str, project_code: str) -> List[Path]:
        cache_key = (
            str(reel_dir).replace("\\", "/").lower(),
            str(shot_name or "").lower(),
            str(sequence or "").lower(),
            str(project_code or "").lower(),
        )
        if cache_key in self._shot_dir_cache:
            return self._shot_dir_cache[cache_key]

        if not reel_dir.exists():
            self._shot_dir_cache[cache_key] = []
            return []

        shot_text = str(shot_name or "").strip()
        shot_norm = self._normalize_key(shot_text)
        shot_core = re.sub(r"^(sh|shot)[_\-\s]*", "", shot_text, flags=re.IGNORECASE)
        shot_core_norm = self._normalize_key(shot_core)
        seq_norm = self._normalize_key(sequence)
        project_short = project_code.split("_", 1)[-1] if "_" in project_code else project_code
        project_norm = self._normalize_key(project_short)

        ranked: List[tuple] = []
        try:
            for child in reel_dir.iterdir():
                if not child.is_dir():
                    continue
                name = child.name
                name_norm = self._normalize_key(name)
                score = 0

                if shot_norm and name_norm == shot_norm:
                    score += 260
                if shot_core_norm:
                    if name_norm.endswith(shot_core_norm):
                        score += 220
                    if f"sh{shot_core_norm}" in name_norm:
                        score += 180
                    if shot_core_norm in name_norm:
                        score += 140
                if shot_norm and shot_norm in name_norm:
                    score += 90
                if seq_norm and seq_norm in name_norm:
                    score += 60
                if project_norm and project_norm in name_norm:
                    score += 40

                if score > 0:
                    ranked.append((score, child))
        except Exception:
            self._shot_dir_cache[cache_key] = []
            return []

        ranked.sort(key=lambda row: (row[0], -len(row[1].name)), reverse=True)
        result = [row[1] for row in ranked[:3]]
        self._shot_dir_cache[cache_key] = result
        return result

    def _hydrate_media_paths(self, shot: ReviewShot, raw_shot: Dict[str, Any], project: Dict[str, Any]):
        from .auto_pull_engine import AutoPullEngine

        engine = AutoPullEngine()
        folder_base = str(project.get("path") or "").strip()
        project_code = str(project.get("code") or "").strip()
        folder_template = project.get("folder_template") or {}
        if not isinstance(folder_template, dict):
            folder_template = {}

        shot_root = None
        if folder_base:
            shot_root = Path(folder_base) / "05_Reels" / str(shot.sequence) / str(shot.name)

        scan_candidates: List[Path] = []
        render_candidates: List[Path] = []

        for key in ("scan",):
            candidate = self._candidate_dir_from_template(folder_base, folder_template.get(key, ""), shot.sequence, shot.name)
            if candidate:
                scan_candidates.append(candidate)

        for key in ("output", "comp"):
            candidate = self._candidate_dir_from_template(folder_base, folder_template.get(key, ""), shot.sequence, shot.name)
            if candidate:
                render_candidates.append(candidate)

        # Fuzzy expansion for real-world naming mismatches (e.g. SC_68 -> REEL_68, SH_001 -> MA2_SC_68_001)
        for root in self._project_root_candidates(project):
            reel_dirs = self._find_reel_dirs(root, shot.sequence)
            for reel_dir in reel_dirs:
                shot_dirs = self._find_shot_dirs(reel_dir, shot.name, shot.sequence, project_code)
                for shot_dir in shot_dirs:
                    # Generic scan/render defaults
                    scan_candidates.extend(
                        [
                            shot_dir / "01_Scan" / "EXR",
                            shot_dir / "01_Scan",
                            shot_dir / "scan",
                            shot_dir / "plates",
                        ]
                    )
                    render_candidates.extend(
                        [
                            shot_dir / "08_Output",
                            shot_dir / "08_Output" / "PREP" / "EXR",
                            shot_dir / "08_Output" / "PREP",
                            shot_dir / "07_Comp",
                            shot_dir / "04_Comp",
                            shot_dir / "comp",
                            shot_dir / "output",
                        ]
                    )

                    for key in ("scan",):
                        suffix = self._template_suffix_after_shot(folder_template.get(key, ""))
                        if suffix is not None:
                            scan_candidates.append(shot_dir / suffix if str(suffix) else shot_dir)

                    for key in ("output", "comp"):
                        suffix = self._template_suffix_after_shot(folder_template.get(key, ""))
                        if suffix is not None:
                            render_candidates.append(shot_dir / suffix if str(suffix) else shot_dir)

        folder_paths = raw_shot.get("folder_paths", {})
        if isinstance(folder_paths, dict):
            for key, path in folder_paths.items():
                if not path:
                    continue
                p = self._resolve_path_value(path)
                if key.lower() in {"scan", "plates"}:
                    scan_candidates.append(p)
                elif key.lower() in {"output", "comp", "render"}:
                    render_candidates.append(p)

        if shot_root:
            scan_candidates.append(shot_root)
            render_candidates.append(shot_root)

        # Deduplicate while preserving order.
        scan_candidates = self._dedupe_paths(scan_candidates)
        render_candidates = self._dedupe_paths(render_candidates)

        def find_media(candidates: List[Path], patterns, kind: str):
            flat_exts = []
            for _, ext_list in patterns:
                for ext in ext_list:
                    if ext not in flat_exts:
                        flat_exts.append(ext)

            # Fast path: inspect candidate directories directly (and common subdirs) without deep recursion.
            for candidate in candidates:
                base = candidate if candidate.is_dir() else candidate.parent
                if not base.exists():
                    continue

                quick_paths = [base, base / "EXR", base / "DPX", base / "exr", base / "dpx"]
                for quick in quick_paths:
                    if not quick.exists():
                        continue
                    info = engine._check_path_for_sequence(quick, "quick", flat_exts, kind)
                    if info:
                        return info.get("full_path") / info.get("pattern"), info

            # Fallback: deep pattern search, limited to first few candidates for performance.
            for candidate in candidates[:3]:
                base = candidate if candidate.is_dir() else candidate.parent
                if not base.exists():
                    continue
                info = engine._find_files(base, patterns, kind)
                if info:
                    return engine._get_full_path(base, info), info
            return None, None

        scan_path, scan_info = find_media(scan_candidates, engine.SCAN_PATTERNS, "scan")
        if scan_path:
            shot.scan_path = scan_path
            if scan_info and scan_info.get("frame_range"):
                shot.frame_range = scan_info.get("frame_range")
            if scan_info and scan_info.get("format"):
                shot.format = scan_info.get("format")

        render_path, _ = find_media(render_candidates, engine.RENDER_PATTERNS, "render")
        if render_path:
            shot.render_path = render_path

    async def _create_review_shot_from_tracking(
        self,
        raw_shot: Dict[str, Any],
        project: Dict[str, Any],
        hydrate_media: bool = False,
    ) -> Optional[ReviewShot]:
        try:
            shot_name = str(raw_shot.get("shot_name") or "").strip()
            if not shot_name:
                return None

            shot = ReviewShot(
                id=str(raw_shot.get("id", "")),
                name=shot_name,
                sequence=str(raw_shot.get("reel_episode") or raw_shot.get("sequence") or "Unknown"),
                project_name=str(project.get("name") or ""),
                reviewer=str(raw_shot.get("assigned_artist") or raw_shot.get("artist_name") or ""),
            )
            shot.status = self._map_status_from_dashboard(str(raw_shot.get("status") or ""))

            notes = self._to_notes_list(raw_shot.get("notes"))
            if not notes:
                feedback_notes: List[str] = []
                for key in ("feedback_internal", "feedback_client", "feedback_director"):
                    entries = raw_shot.get(key) or []
                    if not isinstance(entries, list):
                        continue
                    for entry in entries:
                        if isinstance(entry, dict):
                            text = str(entry.get("text", "")).strip()
                        else:
                            text = str(entry).strip()
                        if text:
                            feedback_notes.append(text)
                notes = feedback_notes
            shot.notes = notes

            if hydrate_media:
                self._hydrate_media_paths(shot, raw_shot, project)
                setattr(shot, "_media_hydrated", True)
            else:
                setattr(shot, "_media_hydrated", False)
            return shot
        except Exception as e:
            logger.error(f"Error creating shot from tracking row: {e}", exc_info=True)
            return None

    async def _create_review_shot_from_legacy_db(self, row, project_name: str = "") -> Optional[ReviewShot]:
        """
        Legacy table adapter: shots(project_name, directory, notes, artist_name, status).
        """
        try:
            shot_id = self._row_get(row, "id", 0)
            shot_name = self._row_get(row, "shot_name", 1)
            sequence = self._row_get(row, "sequence", 2)
            directory = self._row_get(row, "directory", 3)
            notes = self._row_get(row, "notes", 4)
            artist_name = self._row_get(row, "artist_name", 5)
            status = self._row_get(row, "status", 6)

            shot = ReviewShot(
                id=str(shot_id),
                name=shot_name,
                sequence=sequence or "Unknown",
                project_name=project_name,
                reviewer=artist_name or "",
            )
            shot.status = self._map_status_from_dashboard(str(status or ""))
            shot.notes = self._to_notes_list(notes)

            if directory:
                from .auto_pull_engine import AutoPullEngine

                shot_dir = Path(directory)
                engine = AutoPullEngine()
                scan_info = engine._find_files(shot_dir, engine.SCAN_PATTERNS, "scan")
                if scan_info:
                    shot.scan_path = engine._get_full_path(shot_dir, scan_info)
                    shot.frame_range = scan_info.get("frame_range")
                    shot.format = scan_info.get("format", "unknown")

                render_info = engine._find_files(shot_dir, engine.RENDER_PATTERNS, "render")
                if render_info:
                    shot.render_path = engine._get_full_path(shot_dir, render_info)
            return shot
        except Exception as e:
            logger.error(f"Error creating shot from legacy DB row: {e}", exc_info=True)
            return None
    
    async def load_shots_from_dashboard(
        self,
        project_name: Optional[str] = None,
        hydrate_media: bool = False,
    ) -> List[ReviewShot]:
        """
        Load shots from dashboard database
        
        Args:
            project_name: Specific project, or None for active project
            hydrate_media: If True, resolve scan/render paths for every shot eagerly.
        
        Returns:
            List of ReviewShot objects
        """
        logger.info(f"Loading shots from dashboard: {project_name or 'active project'}")
        self._reel_dir_cache.clear()
        self._shot_dir_cache.clear()
        
        try:
            tracking_project = await self._resolve_tracking_project(project_name)
            if tracking_project:
                raw_rows = self.db.get_tracking_shots(tracking_project["code"]) or []
                shots = []
                for raw in raw_rows:
                    shot = await self._create_review_shot_from_tracking(
                        raw,
                        tracking_project,
                        hydrate_media=hydrate_media,
                    )
                    if shot:
                        shots.append(shot)
                self._active_project_code = tracking_project["code"]
                logger.info(
                    "Loaded %s tracking shots for project %s",
                    len(shots),
                    tracking_project["code"],
                )
                if shots:
                    return shots

            # Legacy fallback
            active_legacy = await self._get_active_legacy_project()
            resolved_project_name = project_name or (active_legacy.get("name") if active_legacy else None)
            if not resolved_project_name:
                logger.warning("No active project found")
                return []

            if not await self._table_exists("shots"):
                logger.info("Legacy shots table not present; skipping legacy dashboard fallback.")
                return []

            query = """
                SELECT id, shot_name, sequence, directory, notes, artist_name, status
                FROM shots
                WHERE project_name = %s
                ORDER BY sequence, shot_name
            """
            results = await self.db.execute_query(query, (resolved_project_name,))
            if not results:
                logger.warning("No shots found in dashboard")
                return []

            shots = []
            for row in results:
                shot = await self._create_review_shot_from_legacy_db(row, resolved_project_name or "")
                if shot:
                    shots.append(shot)
            logger.info(f"Loaded {len(shots)} legacy shots from dashboard")
            return shots
        except Exception as e:
            logger.error(f"Error loading shots from dashboard: {e}", exc_info=True)
            return []

    async def ensure_media_paths(self, shot: ReviewShot, project_name: Optional[str] = None, force: bool = False) -> bool:
        """
        Lazily hydrate scan/render paths for a single shot.

        Args:
            force: If True, ignore _media_hydrated flag and re-resolve paths.
        """
        if not shot:
            return False
        if not force and getattr(shot, "_media_hydrated", False):
            return bool(shot.scan_path or shot.render_path)

        project = await self._resolve_tracking_project(project_name or shot.project_name or None)
        if not project:
            setattr(shot, "_media_hydrated", True)
            return False

        shot_name = str(getattr(shot, "name", "") or "").strip()
        raw_shot = {}
        if shot_name:
            try:
                row = await self.db.execute_query(
                    """
                    SELECT data_json
                    FROM tracking_shots
                    WHERE project_code=%s AND shot_name=%s
                    """,
                    (project["code"], shot_name),
                    fetch="one",
                )
                raw_json = row.get("data_json") if isinstance(row, dict) else None
                raw_shot = self._safe_json_load(raw_json)
                if shot_name and not raw_shot.get("shot_name"):
                    raw_shot["shot_name"] = shot_name
                if getattr(shot, "sequence", None) and not raw_shot.get("reel_episode"):
                    raw_shot["reel_episode"] = shot.sequence
            except Exception:
                raw_shot = {
                    "shot_name": shot_name,
                    "reel_episode": getattr(shot, "sequence", ""),
                    "folder_paths": {},
                }

        self._hydrate_media_paths(shot, raw_shot, project)
        setattr(shot, "_media_hydrated", True)
        return bool(shot.scan_path or shot.render_path)
    
    def _map_status_from_dashboard(self, db_status: str) -> ShotStatus:
        """
        Map dashboard status to ReviewShot status
        
        Dashboard might use: pending, in_progress, approved, rejected, etc.
        """
        if not db_status:
            return ShotStatus.PENDING
        
        status_lower = db_status.lower()
        
        mapping = {
            'pending': ShotStatus.PENDING,
            'ready': ShotStatus.PENDING,
            'not started': ShotStatus.PENDING,
            'in_review': ShotStatus.IN_REVIEW,
            'in_progress': ShotStatus.IN_REVIEW,
            'wip': ShotStatus.IN_REVIEW,
            'review': ShotStatus.IN_REVIEW,
            'approved': ShotStatus.APPROVED,
            'final': ShotStatus.APPROVED,
            'done': ShotStatus.APPROVED,
            'delivered': ShotStatus.APPROVED,
            'rejected': ShotStatus.REJECTED,
            'needs_revision': ShotStatus.RE_REVIEW,
            're_review': ShotStatus.RE_REVIEW,
            'retake': ShotStatus.RE_REVIEW,
        }
        
        return mapping.get(status_lower, ShotStatus.PENDING)
    
    def _map_status_to_dashboard(self, status: ShotStatus) -> str:
        """Map ReviewShot status to dashboard status"""
        mapping = {
            ShotStatus.PENDING: 'pending',
            ShotStatus.IN_REVIEW: 'in_review',
            ShotStatus.APPROVED: 'approved',
            ShotStatus.REJECTED: 'rejected',
            ShotStatus.RE_REVIEW: 'needs_revision'
        }
        
        return mapping.get(status, 'pending')

    async def _update_semantic_embedding(self, shot: ReviewShot):
        """Generates a semantic vector for the shot in the background."""
        if self._is_local_fallback_mode(): return
        if not getattr(shot, "name", ""): return
        
        try:
            # Avoid blocking the main asyncio event loop heavily
            text = f"Shot {shot.name} in project {getattr(shot, 'project_code', '')}. Status is {shot.status.name}. Notes: {' '.join(shot.notes)}"
            
            from .vector_service import vector_service
            # Run generator in a thread since fastembed is CPU bound
            loop = asyncio.get_event_loop()
            vec = await loop.run_in_executor(None, vector_service.generate_embedding, text)
            
            if vec:
                vec_str = "[" + ",".join(map(str, vec)) + "]"
                await self.db.execute_query(
                    "UPDATE tracking_shots SET semantic_embedding = %s WHERE project_code=%s AND shot_name=%s AND version=%s",
                    (vec_str, shot.project_code, shot.name, getattr(shot, "version", "v001"))
                )
        except Exception as e:
            logger.debug(f"Failed to generate semantic embedding: {e}")

    async def _sync_tracking_shot(self, shot: ReviewShot, set_status: bool = False, set_notes: bool = False) -> bool:
        project = await self._resolve_tracking_project(shot.project_name or None)
        if not project:
            return False

        shot_name = str(shot.name or "").strip()
        if not shot_name:
            return False

        try:
            row = await self.db.execute_query(
                """
                SELECT data_json, version, priority
                FROM tracking_shots
                WHERE project_code=%s AND shot_name=%s
                """,
                (project["code"], shot_name),
                fetch="one",
            )
            if not row:
                return False

            payload = self._safe_json_load(self._row_get(row, "data_json", 0, "{}"))
            version = int(self._row_get(row, "version", 1, 1) or 1)
            status_value = str(payload.get("status") or "")

            if set_status:
                status_value = self._map_status_to_dashboard(shot.status)
                payload["status"] = status_value
                if shot.reviewer:
                    payload["assigned_artist"] = shot.reviewer

            if set_notes:
                payload["notes"] = "\n".join(shot.notes) if shot.notes else ""

            if not status_value:
                status_value = str(payload.get("status") or "")

            try:
                priority = int(payload.get("priority", self._row_get(row, "priority", 2, 0)) or 0)
            except (TypeError, ValueError):
                priority = 0

            updated = await self.db.execute_query(
                """
                UPDATE tracking_shots
                SET data_json=%s,
                    status=%s,
                    priority=%s,
                    last_updated=%s,
                    version=version+1
                WHERE project_code=%s AND shot_name=%s AND version=%s
                """,
                (
                    json.dumps(payload, default=str),
                    status_value,
                    priority,
                    datetime.now().isoformat(),
                    project["code"],
                    shot_name,
                    version,
                ),
                fetch="rowcount",
            )
            if updated:
                asyncio.create_task(self._update_semantic_embedding(shot))
            return bool(updated and int(updated) > 0)
        except Exception as e:
            logger.error(f"Error syncing tracking shot {shot_name}: {e}", exc_info=True)
            return False
    
    async def sync_shot_status(self, shot: ReviewShot) -> bool:
        """
        Update shot status in dashboard database
        
        Args:
            shot: ReviewShot object
        
        Returns:
            True if successful
        """
        if self._is_local_fallback_mode():
            logger.info("DashboardSync: sync_shot_status skipped in LOCAL MODE for %s", getattr(shot, "name", "unknown"))
            return False
        try:
            if await self._sync_tracking_shot(shot, set_status=True, set_notes=False):
                logger.info(f"Synced tracking status for shot {shot.name}")
                return True

            db_status = self._map_status_to_dashboard(shot.status)
            
            query = """
                UPDATE shots
                SET status = %s,
                    reviewer = %s,
                    review_date = %s
                WHERE id = %s
            """
            
            self.db.execute_update(
                query,
                (db_status, shot.reviewer, datetime.now(), shot.id)
            )
            
            asyncio.create_task(self._update_semantic_embedding(shot))
            logger.info(f"Synced status for shot {shot.name}: {db_status}")
            return True
        
        except Exception as e:
            logger.error(f"Error syncing shot status: {e}", exc_info=True)
            return False
    
    async def sync_shot_notes(self, shot: ReviewShot) -> bool:
        """
        Save shot notes to dashboard database
        
        Args:
            shot: ReviewShot object
        
        Returns:
            True if successful
        """
        if self._is_local_fallback_mode():
            logger.info("DashboardSync: sync_shot_notes skipped in LOCAL MODE for %s", getattr(shot, "name", "unknown"))
            return False
        try:
            if await self._sync_tracking_shot(shot, set_status=False, set_notes=True):
                logger.info(f"Synced tracking notes for shot {shot.name}")
                return True

            # Combine notes into single string
            notes_text = '\n'.join(shot.notes) if shot.notes else ''
            
            query = """
                UPDATE shots
                SET notes = %s
                WHERE id = %s
            """
            
            self.db.execute_update(query, (notes_text, shot.id))
            
            logger.info(f"Synced notes for shot {shot.name}")
            return True
        
        except Exception as e:
            logger.error(f"Error syncing shot notes: {e}", exc_info=True)
            return False
    
    async def sync_all_shots(self, shots: List[ReviewShot]) -> dict:
        """
        Batch sync all shots to dashboard
        
        Args:
            shots: List of ReviewShot objects
        
        Returns:
            Dict with success/failure counts
        """
        if self._is_local_fallback_mode():
            logger.info("DashboardSync: sync_all_shots skipped in LOCAL MODE.")
            return {"success": 0, "failed": 0, "total": len(shots or [])}

        success_count = 0
        failure_count = 0
        
        for shot in shots:
            # Prefer one tracking-table update for status + notes.
            if await self._sync_tracking_shot(shot, set_status=True, set_notes=bool(shot.notes)):
                success_count += 1
            else:
                # Legacy fallback path.
                if await self.sync_shot_status(shot):
                    if shot.notes:
                        await self.sync_shot_notes(shot)
                    success_count += 1
                else:
                    failure_count += 1
        
        logger.info(f"Batch sync: {success_count} success, {failure_count} failed")
        
        return {
            'success': success_count,
            'failed': failure_count,
            'total': len(shots)
        }
    
    async def get_available_projects(self) -> List[dict]:
        """
        Get list of all available projects from dashboard
        
        Returns:
            List of project dicts with name and path
        """
        try:
            tracking = await self._get_tracking_projects()
            if tracking:
                projects = [
                    {"code": p["code"], "name": p["name"], "path": p["path"]}
                    for p in tracking
                ]
                projects.sort(key=lambda p: (p.get("name") or p.get("code") or "").lower())
                return projects

            query = "SELECT name, target_directory FROM projects ORDER BY name"
            results = await self.db.execute_query(query)
            if not results:
                return []

            return [
                {
                    "name": self._row_get(row, "name", 0),
                    "path": self._row_get(row, "target_directory", 1),
                }
                for row in results
            ]
        except Exception as e:
            logger.error(f"Error getting projects: {e}", exc_info=True)
            return []
