import weakref
import logging
from functools import partial
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFileDialog, QInputDialog, QMessageBox, QProgressDialog
import qasync

from .shot_review.workers import AutoPullWorker

try:
    import shiboken6
except Exception:  # pragma: no cover - optional runtime dependency
    shiboken6 = None


class ShotReviewProjectService:
    """Project loading/sync flows extracted from ShotReviewTab."""

    def __init__(self, owner, logger):
        self._owner_ref = weakref.ref(owner)
        self._pull_worker = None
        self.logger = logger

    def _owner(self):
        owner = self._owner_ref()
        if not self._is_qobject_alive(owner):
            return None
        return owner

    @staticmethod
    def _is_qobject_alive(obj):
        if obj is None:
            return False
        if shiboken6 is None:
            return True
        try:
            return shiboken6.isValid(obj)
        except Exception:
            return False

    def _resolve_worker_ref(self, worker_ref):
        worker = worker_ref() if worker_ref else None
        if not self._is_qobject_alive(worker):
            return None
        return worker

    def _finalize_pull_worker(self, worker):
        if worker is not None:
            try:
                worker.deleteLater()
            except RuntimeError as exc:
                self.logger.debug("Pull worker deleteLater skipped: %s", exc)

        if worker is self._pull_worker:
            self._pull_worker = None

        owner = self._owner()
        if owner is not None and getattr(owner, "pull_worker", None) is worker:
            owner.pull_worker = None

    def _on_pull_worker_finished(self, worker_ref, shots):
        worker = self._resolve_worker_ref(worker_ref)
        if worker is None:
            return
        self.on_pull_finished(worker, shots)

    def _on_pull_worker_error(self, worker_ref, error_msg):
        worker = self._resolve_worker_ref(worker_ref)
        if worker is None:
            return
        self.on_pull_error(worker, error_msg)

    def _on_owner_destroyed(self, worker_ref):
        worker = self._resolve_worker_ref(worker_ref)
        if worker is None:
            return
        try:
            if worker.isRunning():
                worker.requestInterruption()
                worker.wait(2000)
        except RuntimeError as exc:
            self.logger.debug("Owner-destroy pull worker interruption skipped: %s", exc)
        self._finalize_pull_worker(worker)

    def _stop_active_pull_worker(self, timeout_ms: int = 2000):
        worker = self._pull_worker
        if not worker:
            return
        if not self._is_qobject_alive(worker):
            self._pull_worker = None
            owner = self._owner()
            if owner is not None and getattr(owner, "pull_worker", None) is worker:
                owner.pull_worker = None
            return
        try:
            if worker.isRunning():
                worker.requestInterruption()
                worker.wait(timeout_ms)
        except RuntimeError as exc:
            self.logger.debug("Pull worker interruption skipped during cleanup: %s", exc)
        self._finalize_pull_worker(worker)

    def cleanup(self, timeout_ms: int = 2000):
        """Public shutdown hook used by owner close paths."""
        self._stop_active_pull_worker(timeout_ms=timeout_ms)

    @staticmethod
    def _close_progress(owner):
        progress = getattr(owner, "progress", None)
        if progress is None:
            return
        try:
            progress.close()
        except RuntimeError as exc:
            logging.debug("Project auto-pull progress dialog close skipped: %s", exc)
        owner.progress = None

    def auto_pull_project(self):
        owner = self._owner()
        if owner is None:
            return

        folder = QFileDialog.getExistingDirectory(
            owner,
            "Select Project Folder",
            str(Path.home()),
        )
        if folder:
            self.load_project(Path(folder))

    def load_project(self, project_path: Path):
        o = self._owner()
        if o is None:
            return

        self.logger.info("Loading project: %s", project_path)
        self._stop_active_pull_worker()
        self._close_progress(o)

        o.project_path = project_path
        o.current_project_path = project_path
        o.current_project_name = project_path.name
        o.project_label.setText(f"Project: {project_path.name}")

        o.progress = QProgressDialog(
            f"Scanning {project_path.name}...\nThis may take a moment for deep structures.",
            "Cancel",
            0,
            0,
            o,
        )
        o.progress.setWindowModality(Qt.WindowModality.WindowModal)
        o.progress.setMinimumDuration(0)
        o.progress.show()

        o.pull_worker = AutoPullWorker(o.engine, project_path)
        self._pull_worker = o.pull_worker
        worker_ref = weakref.ref(o.pull_worker)
        o.pull_worker.finished.connect(partial(self._on_pull_worker_finished, worker_ref))
        o.pull_worker.error.connect(partial(self._on_pull_worker_error, worker_ref))
        o.destroyed.connect(partial(self._on_owner_destroyed, worker_ref))
        o.pull_worker.start()

    def on_pull_finished(self, worker_or_shots, shots=None):
        # Backward-compatible signature:
        # - on_pull_finished(worker, shots) for worker signal wiring
        # - on_pull_finished(shots) for legacy wrapper calls
        worker = worker_or_shots if shots is not None else self._pull_worker
        resolved_shots = shots if shots is not None else worker_or_shots

        if worker is not self._pull_worker:
            self._finalize_pull_worker(worker)
            return
        self._finalize_pull_worker(worker)

        o = self._owner()
        if o is None:
            return

        self._close_progress(o)
        o.shots = resolved_shots
        if o.current_project_name:
            for shot in o.shots:
                if not getattr(shot, "project_name", ""):
                    shot.project_name = o.current_project_name

        o.update_shot_list()
        o.update_stats()

        if o.engine:
            stats = o.engine.get_detection_stats()
            msg = f"Found {stats['total_shots']} shots\n\n"
            msg += f"Complete: {stats['complete_shots']}\n"
            msg += f"Missing scan: {stats['missing_scan']}\n"
            msg += f"Missing render: {stats['missing_render']}"
        else:
            msg = f"Found {len(resolved_shots)} shots"

        QMessageBox.information(o, "Project Loaded", msg)
        self.logger.info("Loaded %s shots", len(o.shots))

    def on_pull_error(self, worker_or_error, error_msg=None):
        # Backward-compatible signature:
        # - on_pull_error(worker, error_msg) for worker signal wiring
        # - on_pull_error(error_msg) for legacy wrapper calls
        worker = worker_or_error if error_msg is not None else self._pull_worker
        resolved_error = error_msg if error_msg is not None else worker_or_error

        if worker is not self._pull_worker:
            self._finalize_pull_worker(worker)
            return
        self._finalize_pull_worker(worker)

        o = self._owner()
        if o is None:
            return

        self._close_progress(o)
        self.logger.error("Error loading project: %s", resolved_error)
        QMessageBox.critical(o, "Error", f"Failed to load project:\n{resolved_error}")

    def refresh_project(self):
        o = self._owner()
        if o is None:
            return

        if o.project_path:
            self.load_project(o.project_path)
        else:
            QMessageBox.information(
                o,
                "No Project",
                "Please load a project first using Auto-Pull or Dashboard",
            )

    def apply_loaded_shots(self, shots, project_name: str = "", project_path=None):
        o = self._owner()
        if o is None:
            return

        o.shots = shots
        o._media_checked_shots.clear()
        if project_name:
            o.current_project_name = project_name
        if project_path:
            o.current_project_path = Path(project_path) if not isinstance(project_path, Path) else project_path
            o.project_path = o.current_project_path
        if o.current_project_name:
            for shot in o.shots:
                if not getattr(shot, "project_name", ""):
                    shot.project_name = o.current_project_name
        o.update_shot_list()
        o.update_stats()

    @qasync.asyncSlot()
    async def auto_load_from_dashboard(self):
        o = self._owner()
        if o is None:
            return

        try:
            shots = await o.dashboard_sync.load_shots_from_dashboard(hydrate_media=False)
            if shots:
                active_project = await o.dashboard_sync.get_active_project()
                pname = active_project.get("name", "") if active_project else ""
                ppath = active_project.get("path") if active_project else None
                self.apply_loaded_shots(shots, pname, ppath)
                if active_project:
                    o.project_label.setText(f"Project: {pname} (from dashboard)")
                self.logger.info("Auto-loaded %s shots from dashboard", len(shots))
        except Exception as exc:
            self.logger.warning("Could not auto-load from dashboard: %s", exc)

    @qasync.asyncSlot()
    async def load_from_dashboard(self):
        o = self._owner()
        if o is None:
            return

        projects = await o.dashboard_sync.get_available_projects()
        if not projects:
            QMessageBox.warning(o, "No Projects", "No projects found in dashboard database.")
            return

        project_names = [p["name"] for p in projects]
        project_name, ok = QInputDialog.getItem(
            o,
            "Select Project",
            "Choose project to load:",
            project_names,
            0,
            False,
        )
        if not (ok and project_name):
            return

        shots = await o.dashboard_sync.load_shots_from_dashboard(project_name, hydrate_media=False)
        if shots:
            selected_project = next((p for p in projects if p.get("name") == project_name), None)
            selected_path = selected_project.get("path") if selected_project else None
            self.apply_loaded_shots(shots, project_name, selected_path)
            o.project_label.setText(f"Project: {project_name} (from dashboard)")

            complete = sum(1 for s in shots if s.is_complete())
            stats = f"Loaded {len(shots)} shots from dashboard\n\n"
            stats += f"Complete: {complete}\n"
            stats += f"Missing files: {len(shots) - complete}"
            QMessageBox.information(o, "Success", stats)
            self.logger.info("Loaded %s shots from dashboard: %s", len(shots), project_name)
        else:
            QMessageBox.warning(o, "No Shots", f"No shots found for project: {project_name}")

    def get_project_context(self) -> dict:
        o = self._owner()
        if o is None:
            return {"name": "", "path": None}
        return {"name": o.current_project_name or "", "path": o.current_project_path}

    @qasync.asyncSlot()
    async def sync_to_dashboard(self):
        o = self._owner()
        if o is None:
            return

        if not o.shots:
            QMessageBox.information(o, "No Shots", "No shots loaded to sync")
            return

        reply = QMessageBox.question(
            o,
            "Sync to Dashboard",
            f"Save review statuses for {len(o.shots)} shots to dashboard?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        result = await o.dashboard_sync.sync_all_shots(o.shots)
        msg = "Sync complete!\n\n"
        msg += f"Success: {result['success']}\n"
        msg += f"Failed: {result['failed']}\n"
        msg += f"Total: {result['total']}"
        QMessageBox.information(o, "Sync Complete", msg)
        self.logger.info("Synced %s/%s shots to dashboard", result["success"], result["total"])
