from PySide6.QtWidgets import QMessageBox
from PySide6.QtCore import Qt
from ..workers import ShotProxyRenderWorker
from ut_vfx.core.domain.review_shot import ReviewShot
from pathlib import Path
from functools import partial
import logging
logger = logging.getLogger(__name__)

def start_proxy_render(tab, shot: ReviewShot):
    """Render scan/render MP4 proxies after approval."""
    if not shot or tab._is_closing:
        return

    if tab.proxy_render_worker and tab.proxy_render_worker.isRunning():
        shot_id = str(getattr(shot, "id", ""))
        if any(str(getattr(queued, "id", "")) == shot_id for queued in tab.proxy_render_queue):
            return

        shot.proxy_status = "queued"
        tab.proxy_render_queue.append(shot)
        if tab.current_shot and str(tab.current_shot.id) == shot_id:
            tab.display_shot(tab.current_shot)
        logger.info(f"Queued MP4 proxy render for {shot.name} (queue size: {len(tab.proxy_render_queue)})")
        return

    tab._start_proxy_render_worker(shot)

def _start_proxy_render_worker(tab, shot: ReviewShot):
    """Start a proxy render worker for one shot."""
    if not shot or tab._is_closing:
        return

    from PySide6.QtWidgets import QProgressDialog

    shot.proxy_status = "generating"
    if tab.current_shot and str(tab.current_shot.id) == str(shot.id):
        tab.display_shot(tab.current_shot)

    tab.proxy_progress_dialog = QProgressDialog("Preparing MP4 proxy render...", "", 0, 100, tab)
    tab.proxy_progress_dialog.setWindowTitle("MP4 Proxy Render")
    tab.proxy_progress_dialog.setWindowModality(Qt.WindowModality.WindowModal)
    tab.proxy_progress_dialog.setAutoClose(False)
    tab.proxy_progress_dialog.setMinimumDuration(0)
    tab.proxy_progress_dialog.setCancelButton(None)
    tab.proxy_progress_dialog.setValue(0)
    tab.proxy_progress_dialog.show()

    project_name = tab.current_project_name or getattr(shot, 'project_name', '') or "Unknown_Project"
    tab._stop_thread_worker("proxy_render_worker")
    worker = ShotProxyRenderWorker(shot, tab.config, project_name)
    worker.progress.connect(tab.on_proxy_render_progress)
    worker.completed.connect(tab.on_proxy_render_finished)
    worker.finished.connect(partial(tab._release_finished_worker, "proxy_render_worker", worker))
    tab.proxy_render_worker = worker
    worker.start()

def on_proxy_render_progress(tab, value: int, message: str):
    """Update the MP4 proxy progress UI."""
    worker = tab.sender()
    if tab._is_closing or worker is not tab.proxy_render_worker:
        return
    if tab.proxy_progress_dialog:
        tab.proxy_progress_dialog.setLabelText(message)
        tab.proxy_progress_dialog.setValue(max(0, min(100, value)))

def on_proxy_render_finished(tab, success: bool, message: str, scan_proxy: str, render_proxy: str, shot_id: str):
    """Finalize MP4 proxy render and update shot state."""
    worker = tab.sender()
    if tab._is_closing or (worker is not None and worker is not tab.proxy_render_worker):
        return

    if tab.proxy_progress_dialog:
        tab.proxy_progress_dialog.setValue(100)
        tab.proxy_progress_dialog.close()
        tab.proxy_progress_dialog = None
    if worker is tab.proxy_render_worker:
        tab.proxy_render_worker = None

    target_shot = next((s for s in tab.shots if str(s.id) == str(shot_id)), None)
    if not target_shot:
        target_shot = tab.current_shot
    if not target_shot:
        return

    if scan_proxy:
        target_shot.scan_proxy_path = Path(scan_proxy)
    if render_proxy:
        target_shot.render_proxy_path = Path(render_proxy)

    target_shot.proxy_status = "ready" if success else "failed"
    if tab.current_shot and str(tab.current_shot.id) == str(target_shot.id):
        tab.display_shot(tab.current_shot)

    if not success and not tab._is_closing:
        QMessageBox.warning(tab, "Proxy Render Failed", message)
    else:
        logger.info(message)

    if not tab._is_closing and tab.proxy_render_queue:
        next_shot = tab.proxy_render_queue.pop(0)
        tab._start_proxy_render_worker(next_shot)
