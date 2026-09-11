"""
VFX Supervisor Review Tool - Main Tab

Professional review tool for comparing scan (plates) vs render (comp output).
Supports auto-detection, manual file selection, continuity checking,
and dual-layer lineup building.
"""

from pathlib import Path
from functools import partial
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QLabel, QMessageBox, QListWidgetItem
)
from PySide6.QtCore import Qt, Signal
import logging

logger = logging.getLogger(__name__)

# Import design tokens for theming
from ...core.infra.design_tokens import ColorTokens as C, TypographyTokens as T, SpacingTokens as S

from ...core.domain.auto_pull_engine import AutoPullEngine
from ...core.domain.review_shot import ReviewShot, ShotStatus
from ...core.domain.dashboard_sync import DashboardSync
from ...core.domain.continuity_checker import ContinuityChecker
from ...core.domain.enhanced_notes import EnhancedNote, NoteCategory, NotePriority, NotesManager
from .shot_review_batch_service import ShotReviewBatchService
from .shot_review_layout_builder import (
    create_control_panel as build_control_panel,
    create_shot_list_panel as build_shot_list_panel,
    create_viewer_panel as build_viewer_panel,
    setup_shot_review_ui,
)
from .shot_review_project_service import ShotReviewProjectService
from .shot_review.tech_check_dialog import TechCheckDialog
from .shot_review.workers import ShotProxyRenderWorker, FrameCacheWorker
from .shot_review.render_selector_dialog import RenderSelectorDialog, apply_render_selection
from ..components.qt_safety import safe_single_shot
from .shot_review.controllers.pagination_controller import (previous_shot, next_shot, next_page, prev_page)
from .shot_review.controllers.cache_controller import (cache_all_frames, _cancel_cache_worker, _on_cache_progress, _on_cache_finished, clear_cache, update_cache_stats)
from .shot_review.controllers.proxy_render_controller import (start_proxy_render, _start_proxy_render_worker, on_proxy_render_progress, on_proxy_render_finished)

class ShotReviewTab(QWidget):

    """
    VFX Supervisor Review Tool (DEBUG MODE)
    """
    
    shot_selected = Signal(ReviewShot)  # Signal for shot selection events
    
    
    def __init__(self, config_manager):
        super().__init__()
        
        # Dynamically assigned UI elements from layout builder
        self.btn_prev_page = None
        self.btn_next_page = None
        self.btn_cache_shot = None
        self.btn_manual_render = None
        self.btn_check_updates = None
        self.btn_approve = None
        self.btn_reject = None
        self.btn_add_note = None
        self.info_label = None
        self.shot_list = None
        self.stats_label = None
        self.live_tech_check = None
        self.comparison_viewer = None
        self.version_combo = None
        self.btn_play_dailies = None
        
        try:
            self.config = config_manager
            
            logger.info("Initializing helpers...")
            self.engine = AutoPullEngine()
            self.dashboard_sync = DashboardSync()
            self.continuity_checker = ContinuityChecker()
            self.notes_manager = NotesManager() 
            self.batch_service = ShotReviewBatchService()
            self.project_service = ShotReviewProjectService(self, logger)
            
            self.shots = []  # Full shot list (not displayed)
            self.current_shot = None
            self.project_path = None
            self.current_project_name = ""
            self.current_project_path = None
            self.proxy_render_worker = None
            self.proxy_progress_dialog = None
            self.proxy_render_queue = []
            self._media_checked_shots = set()
            self._cache_worker = None
            self._cache_progress = None
            self._is_closing = False
            self._is_cleaned = False
            
            # Pagination state
            self.all_shots = []  # Full shot list
            self.displayed_shots = []  # Currently displayed
            self.shots_per_page = 50  # Load 50 at a time
            self.current_page = 0
            
            self.setup_ui()
            
            self.setup_shortcuts()
            
            # Memory monitoring timer
            from PySide6.QtCore import QTimer
            self.cache_monitor_timer = QTimer()
            self.cache_monitor_timer.timeout.connect(self.update_cache_stats)
            self.cache_monitor_timer.start(2000)  # Update every 2 seconds
            
            # Auto-load moved to showEvent for UI responsiveness
            # self.auto_load_from_dashboard()
            self._has_loaded_initial_data = False
            
            logger.info("Shot Review Tab initialized")
            
        except Exception as e:
            logger.error(f"CRITICAL ERROR initializing Shot Review Tab: {e}", exc_info=True)
            # Ensure layout layout exists to show error
            if not self.main_layout():
                QVBoxLayout(self)
            self.main_layout().addWidget(QLabel(f"Error loading Shot Review:\n{str(e)}"))
    
    def showEvent(self, event):
        """Lazy load data when tab actually becomes visible"""
        super().showEvent(event)
        # Restart cache monitor timer when tab becomes visible again
        if hasattr(self, 'cache_monitor_timer') and not self.cache_monitor_timer.isActive():
            self.cache_monitor_timer.start(2000)
        if not hasattr(self, '_has_loaded_initial_data') or not self._has_loaded_initial_data:
            self._has_loaded_initial_data = True
            # Use singleShot to let UI render first, then load data
            safe_single_shot(100, self, self.auto_load_from_dashboard)
    
    def hideEvent(self, event):
        """Stop timers when tab is not visible to save resources."""
        super().hideEvent(event)
        if hasattr(self, 'cache_monitor_timer') and self.cache_monitor_timer.isActive():
            self.cache_monitor_timer.stop()
    
    def setup_ui(self):
        """Create 3-panel layout."""
        setup_shot_review_ui(self)
    
    def create_shot_list_panel(self):
        """Left panel - shot list and filters."""
        return build_shot_list_panel(self)

    def show_context_menu(self, pos):
        """Show context menu for manual overrides"""
        from PySide6.QtWidgets import QMenu
        item = self.shot_list.itemAt(pos)
        if not item:
            # General menu (e.g. Add Shot Manually - Future)
            return
            
        shot = item.data(Qt.ItemDataRole.UserRole)
        
        menu = QMenu(self)
        
        # Actions
        action_open = menu.addAction("Open in Explorer")
        menu.addSeparator()
        
        action_set_scan = menu.addAction("Manually Link Scan...")
        action_set_render = menu.addAction("Manually Link Render...")
        
        # Execute
        action = menu.exec(self.shot_list.mapToGlobal(pos))
        
        if action == action_open:
            self.open_shot_folder(item)
        elif action == action_set_scan:
            self.manual_link_file(shot, "scan")
        elif action == action_set_render:
            self.manual_link_file(shot, "render")

    def manual_link_file(self, shot, type_key):
        """Manual backdoor to link files"""
        from PySide6.QtWidgets import QFileDialog
        
        # Determine current path
        start_path = str(Path.home())
        if shot.scan_path and shot.scan_path.exists():
            start_path = str(shot.scan_path.parent)
        elif self.project_path:
             start_path = str(self.project_path)
             
        # Ask user for file
        file_filter = (
            "All Supported (*.exr *.dpx *.png *.jpg *.jpeg *.tif *.tiff *.mov *.mp4 *.mkv *.avi *.mxf *.webm);;"
            "Image Sequences (*.exr *.dpx *.png *.jpg *.jpeg *.tif *.tiff);;"
            "Video Files (*.mov *.mp4 *.mkv *.avi *.mxf *.webm);;"
            "All Files (*)"
        )
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            f"Select {type_key.upper()} File (Image frame or Video)",
            start_path,
            file_filter,
            options=QFileDialog.Option.DontUseNativeDialog
        )
        
        if file_path:
            path = Path(file_path)
            # We assume sequence, so path is one frame
            # Update shot object
            if type_key == 'scan':
                shot.scan_path = path
                logger.info(f"Manually linked Scan for {shot.name}: {path}")
            elif type_key == 'render':
                shot.render_path = path
                logger.info(f"Manually linked Render for {shot.name}: {path}")
            
            # Clear hydration cache so paths are not stale
            setattr(shot, "_media_hydrated", False)
            shot_key = str(getattr(shot, "id", "") or f"{getattr(shot, 'sequence', '')}:{getattr(shot, 'name', '')}")
            self._media_checked_shots.discard(shot_key)
            
            # Refresh Display
            self.display_shot(shot)
            QMessageBox.information(self, "Manual Link", f"Successfully linked {type_key}!\n{path.name}")
    
    def create_viewer_panel(self):
        """Center panel - comparison viewer."""
        return build_viewer_panel(self)
    
    def create_control_panel(self):
        """Right panel - shot info and controls."""
        return build_control_panel(self)

    def auto_pull_project(self):
        """Auto-detect shots from project folder"""
        self.project_service.auto_pull_project()
    
    
    def load_project(self, project_path: Path):
        """Load and analyze project (Async)"""
        self.project_service.load_project(project_path)
        
    def on_pull_finished(self, shots):
        """Handle successful pull"""
        self.project_service.on_pull_finished(shots)

    def on_pull_error(self, error_msg):
        """Handle pull error"""
        self.project_service.on_pull_error(error_msg)


    
    def refresh_project(self):
        """Refresh current project"""
        self.project_service.refresh_project()
    
    def _apply_loaded_shots(self, shots, project_name: str = "", project_path=None):
        """Common helper: apply loaded shots to the tab state."""
        self.project_service.apply_loaded_shots(shots, project_name, project_path)

    def auto_load_from_dashboard(self):
        """Auto-load shots from dashboard on tab open"""
        self.project_service.auto_load_from_dashboard()
    
    def load_from_dashboard(self):
        """Load shots from dashboard database"""
        self.project_service.load_from_dashboard()

    def get_project_context(self) -> dict:
        """Current project context for downstream lineup/export tools."""
        return self.project_service.get_project_context()
    
    def sync_to_dashboard(self):
        """Sync review statuses back to dashboard"""
        self.project_service.sync_to_dashboard()
    
    def update_shot_list(self):
        """Refresh shot list widget"""
        self.shot_list.clear()
        
        for shot in self.shots:
            # Create list item with status icon
            icon = self.get_status_icon(shot.status)
            warning = ""
            if getattr(shot, "_media_hydrated", False) and not shot.is_complete():
                warning = " [MISSING]"
            text = f"{icon} {shot.name}{warning}"
            
            item = QListWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, shot)
            
            self.shot_list.addItem(item)
    
    def update_stats(self):
        """Update statistics label"""
        self.stats_label.setText(self.batch_service.stats_text(self.shots))
    
    def get_status_icon(self, status: ShotStatus) -> str:
        """Get emoji icon for status"""
        return self.batch_service.status_icon(status)
    
    def on_shot_selected(self, item: QListWidgetItem):
        """Handle shot selection"""
        shot = item.data(Qt.ItemDataRole.UserRole)
        self.current_shot = shot
        self.display_shot(shot)
        
        # Enable/disable buttons
        self.btn_approve.setEnabled(True)
        self.btn_reject.setEnabled(True)
        self.btn_add_note.setEnabled(True)
        self.btn_cache_shot.setEnabled(True)  # Enable cache button
        self.btn_manual_render.setEnabled(True)  # Enable manual render selection
        
        self.shot_selected.emit(shot)
    
    def open_shot_folder(self, item: QListWidgetItem):
        """Open shot directory in file explorer"""
        shot = item.data(Qt.ItemDataRole.UserRole)
        if not shot: return
        
        path_to_open = None
        if shot.scan_path:
            path_to_open = shot.scan_path.parent
        elif shot.render_path:
            path_to_open = shot.render_path.parent
            
        if path_to_open and path_to_open.exists():
            import os
            try:
                os.startfile(str(path_to_open))
                logger.info(f"Opened folder: {path_to_open}")
            except Exception as e:
                logger.error(f"Failed to open folder: {e}")
        else:
             QMessageBox.warning(self, "Missing Folder", f"Folder not found for shot: {shot.name}")

    def display_shot(self, shot: ReviewShot):
        """Display selected shot information (CRASH-SAFE)"""
        try:
            if not shot:
                self.info_label.setPlainText("No shot selected")
                return
                
            self._ensure_shot_media_paths(shot)

            info = f"Shot: {shot.name}\n"
            info += f"Sequence: {shot.sequence}\n"
            info += f"Status: {shot.status.value.upper()}\n"
            info += "-" * 30 + "\n"
            
            # Format
            if hasattr(shot, 'format'):
                info += f"Format: {shot.format.upper()}\n"
            
            # Frame range
            if shot.frame_range:
                start, end = shot.frame_range
                count = shot.get_frame_count()
                info += f"Frames: {start}-{end} ({count} frames)\n"
            else:
                info += "Frames: Unknown\n"
            
            info += "-" * 30 + "\n"
            
            # File status
            if shot.has_scan():
                info += "Scan: Found\n"
            else:
                info += "Scan: Missing\n"
            
            if shot.has_render():
                info += "Render: Found\n"
            else:
                info += "Render: Missing\n"

            if getattr(shot, "scan_proxy_path", None) or getattr(shot, "render_proxy_path", None):
                info += f"Proxy Status: {getattr(shot, 'proxy_status', 'none')}\n"
                if getattr(shot, "scan_proxy_path", None):
                    info += f"  . Scan MP4: {Path(shot.scan_proxy_path).name}\n"
                if getattr(shot, "render_proxy_path", None):
                    info += f"  . Render MP4: {Path(shot.render_proxy_path).name}\n"
            
            # Notes
            if shot.notes:
                info += "-" * 30 + "\n"
                info += f"Notes ({len(shot.notes)}):\n"
                for note in shot.notes[-3:]:  # Show last 3 notes
                    info += f"  - {note}\n"
            
            self.info_label.setPlainText(info)
            
            # Run Live Tech Check (with safety)
            try:
                if hasattr(self, 'live_tech_check'):
                    self.live_tech_check.check_shot(shot)
            except Exception as tech_err:
                logger.warning(f"Tech check failed: {tech_err}")
            
            # Load in comparison viewer (with safety)
            try:
                if hasattr(self, 'comparison_viewer'):
                    self.comparison_viewer.load_shot(shot)
            except Exception as viewer_err:
                logger.error(f"Viewer load failed: {viewer_err}", exc_info=True)
                self.info_label.setPlainText(info + f"\n\n❌ Viewer Error: {str(viewer_err)[:50]}")
                
        except Exception as e:
            logger.error(f"Display shot failed: {e}", exc_info=True)
            try:
                self.info_label.setPlainText(f"Error displaying shot:\n{str(e)[:100]}")
            except Exception as inner_e:
                logger.debug(f"Failed to set error text on info_label: {inner_e}")

    def _ensure_shot_media_paths(self, shot: ReviewShot):
        """Resolve scan/render only when user actually opens a shot."""
        if not shot:
            return
        # Only skip if BOTH paths are already resolved (not just one)
        if getattr(shot, "scan_path", None) and getattr(shot, "render_path", None):
            setattr(shot, "_media_hydrated", True)
            return
        shot_key = str(getattr(shot, "id", "") or f"{getattr(shot, 'sequence', '')}:{getattr(shot, 'name', '')}")
        if shot_key in self._media_checked_shots or getattr(shot, "_media_hydrated", False):
            return

        try:
            self.dashboard_sync.ensure_media_paths(shot, self.current_project_name or shot.project_name)
        except Exception as e:
            logger.debug(f"Lazy media hydration failed for {shot.name}: {e}")
        finally:
            self._media_checked_shots.add(shot_key)
    
    def on_frame_changed(self, frame: int):
        """Handle frame change from comparison viewer"""
        logger.debug(f"Frame changed to: {frame}")
    
    def approve_shot(self):
        """Approve current shot"""
        if self.current_shot:
            self.current_shot.status = ShotStatus.APPROVED
            self.update_shot_list()
            self.update_stats()
            self.display_shot(self.current_shot)
            
            # Auto-sync to dashboard
            self.dashboard_sync.sync_shot_status(self.current_shot)
            self.start_proxy_render(self.current_shot)
            
            logger.info(f"Approved shot: {self.current_shot.name}")
    
    def reject_shot(self):
        """Reject current shot"""
        if self.current_shot:
            self.current_shot.status = ShotStatus.REJECTED
            self.update_shot_list()
            self.update_stats()
            self.display_shot(self.current_shot)
            
            # Auto-sync to dashboard
            self.dashboard_sync.sync_shot_status(self.current_shot)
            
            logger.info(f"Rejected shot: {self.current_shot.name}")

    def start_proxy_render(self, shot: ReviewShot):
        return start_proxy_render(self, shot)

    def _stop_thread_worker(self, attr_name: str, timeout_ms: int = 2000):
        """Request interruption and wait for worker shutdown."""
        worker = getattr(self, attr_name, None)
        if not worker:
            return

        try:
            if hasattr(worker, "isRunning") and worker.isRunning():
                if hasattr(worker, "requestInterruption"):
                    worker.requestInterruption()
                if hasattr(worker, "quit"):
                    worker.quit()
                if hasattr(worker, "wait") and not worker.wait(timeout_ms):
                    logger.warning(f"{attr_name} did not stop in time")
        except Exception as e:
            logger.debug(f"Failed to stop worker {attr_name}: {e}")
        finally:
            try:
                worker.deleteLater()
            except RuntimeError as exc:
                logging.debug("Worker deleteLater skipped during %s cleanup: %s", attr_name, exc)
            setattr(self, attr_name, None)

    def _release_finished_worker(self, attr_name: str, worker):
        """Clear worker reference only when the finished sender matches."""
        if getattr(self, attr_name, None) is worker:
            setattr(self, attr_name, None)
        try:
            worker.deleteLater()
        except RuntimeError as exc:
            logging.debug("Finished worker deleteLater skipped for %s: %s", attr_name, exc)

    def _start_proxy_render_worker(self, shot: ReviewShot):
        return _start_proxy_render_worker(self, shot)

    def on_proxy_render_progress(self, value: int, message: str):
        return on_proxy_render_progress(self, value, message)

    def on_proxy_render_finished(self, success: bool, message: str, scan_proxy: str, render_proxy: str, shot_id: str):
        return on_proxy_render_finished(self, success, message, scan_proxy, render_proxy, shot_id)
    
    def add_note(self):
        """Add enhanced note to current shot"""
        if not self.current_shot:
            return
        
        from PySide6.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QLabel, QTextEdit, QComboBox, QPushButton
        
        # Create enhanced notes dialog
        dialog = QDialog(self)
        dialog.setWindowTitle(f"Add Note - {self.current_shot.name}")
        dialog.setMinimumWidth(500)
        
        layout = QVBoxLayout(dialog)
        
        # Category
        cat_layout = QHBoxLayout()
        cat_layout.addWidget(QLabel("Category:"))
        category_combo = QComboBox()
        category_combo.addItems([c.value for c in NoteCategory])
        cat_layout.addWidget(category_combo)
        layout.addLayout(cat_layout)
        
        # Priority
        pri_layout = QHBoxLayout()
        pri_layout.addWidget(QLabel("Priority:"))
        priority_combo = QComboBox()
        priority_combo.addItems([p.value for p in NotePriority])
        priority_combo.setCurrentText('medium')
        pri_layout.addWidget(priority_combo)
        layout.addLayout(pri_layout)
        
        # Note text
        layout.addWidget(QLabel("Note (use @name to mention):"))
        note_text = QTextEdit()
        note_text.setMinimumHeight(100)
        layout.addWidget(note_text)
        
        # Buttons
        btn_layout = QHBoxLayout()
        btn_save = QPushButton("Save")
        btn_cancel = QPushButton("Cancel")
        btn_save.clicked.connect(dialog.accept)
        btn_cancel.clicked.connect(dialog.reject)
        btn_layout.addWidget(btn_save)
        btn_layout.addWidget(btn_cancel)
        layout.addLayout(btn_layout)
        
        if dialog.exec() == QDialog.DialogCode.Accepted:
            # Create enhanced note
            text = note_text.toPlainText()
            if text:
                enhanced_note = EnhancedNote(
                    text=text,
                    author=self.config.get('username', 'Unknown'),
                    category=NoteCategory(category_combo.currentText()),
                    priority=NotePriority(priority_combo.currentText()),
                    mentions=self.notes_manager.parse_mentions(text)
                )
                
                # Add to shot
                self.current_shot.notes.append(str(enhanced_note))
                self.display_shot(self.current_shot)
                
                # Sync to dashboard
                self.dashboard_sync.sync_shot_notes(self.current_shot)
                
                logger.info(f"Added enhanced note to {self.current_shot.name}")
    
    def open_tech_check(self):
        """Open technical check dialog"""
        if not self.current_shot:
            return
            
        dialog = TechCheckDialog(self.current_shot, self)
        dialog.exec()
    
    def choose_manual_render(self):
        """Manually select render folder/sequence with multi-type detection."""
        if not self.current_shot:
            return
        
        dialog = RenderSelectorDialog(
            self.current_shot,
            project_path=self.project_path,
            parent=self
        )
        
        if not dialog.exec():
            return  # User cancelled
        
        render_path, found_sequence, is_video = dialog.selected_render
        
        # Apply selection to shot model
        apply_render_selection(self.current_shot, render_path, found_sequence, is_video)
        
        # Refresh display and clear cache
        self.display_shot(self.current_shot)
        self.clear_cache()
        self.comparison_viewer.load_shot(self.current_shot)
        
        logger.info(f"Manually selected render: {render_path}")
        
        # Build info message
        info_msg = f"Successfully loaded render from:\n{dialog.selected_folder}\n\n"
        if is_video:
            info_msg += "Render source: Video file\n"
        else:
            info_msg += f"Render frames: {found_sequence['first_frame']}-{found_sequence['last_frame']}\n"
        if self.current_shot.frame_range:
            info_msg += f"Playable range: {self.current_shot.frame_range[0]}-{self.current_shot.frame_range[1]}\n"
        info_msg += f"Format: {self.current_shot.format}"
        
        QMessageBox.information(self, "Render Updated", info_msg)

    def cache_all_frames(self):
        return cache_all_frames(self)

    def _cancel_cache_worker(self):
        return _cancel_cache_worker(self)

    def _on_cache_progress(self, cached_count, total_frames):
        return _on_cache_progress(self, cached_count, total_frames)

    def _on_cache_finished(self, cached_count, total_frames, shot_id):
        return _on_cache_finished(self, cached_count, total_frames, shot_id)
    
    def clear_cache(self):
        return clear_cache(self)
        
        
    def check_smart_updates(self):
        """
        Smart Feature: Check for newer versions of approved shots.
        """
        if not self.shots:
            return
            
        logger.info("Starting Smart Version Scan...")
        
        updates_found = 0
        updated_shots = []
        
        # Only check Active or Approved shots (ignore Rejected?)
        # Let's check ALL shots to be safe, but prioritize Approved ones for notification
        
        from PySide6.QtWidgets import QProgressDialog
        progress = QProgressDialog("Scanning for new versions...", "Cancel", 0, len(self.shots), self)
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        
        for i, shot in enumerate(self.shots):
            if progress.wasCanceled():
                break
            progress.setValue(i)
            
            # Check for updates
            updates = self.engine.check_for_new_version(shot)
            
            if updates:
                updates_found += 1
                updated_shots.append(shot.name)
                
                # Apply updates
                note_msg = "Smart Update: "
                if 'scan_ver' in updates:
                    shot.scan_path = updates['scan_path']
                    note_msg += f"Scan v{updates['scan_ver']} "
                    
                if 'render_ver' in updates:
                    shot.render_path = updates['render_path']
                    note_msg += f"Render v{updates['render_ver']} "
                
                # If shot was Approved, flag it
                if shot.status == ShotStatus.APPROVED:
                    shot.status = ShotStatus.RE_REVIEW
                    shot.notes.append(f"{note_msg} (Creating Re-Review)")
                else:
                    shot.notes.append(note_msg)
                    
                logger.info(f"Updated {shot.name}: {note_msg}")
        
        progress.setValue(len(self.shots))
        
        if updates_found > 0:
            self.update_shot_list()
            self.update_stats()
            # Refresh current shot display if it was updated
            if self.current_shot and self.current_shot.name in updated_shots:
                self.display_shot(self.current_shot)
                
            QMessageBox.information(
                self, 
                "Smart Scan Complete", 
                f"Found and applied updates for {updates_found} shots!\n\n" + "\n".join(updated_shots[:10])
            )
        else:
            QMessageBox.information(self, "Smart Scan", "Detailed scan complete. No new versions found.")

    def previous_shot(self):
        return previous_shot(self)
    
    def next_shot(self):
        return next_shot(self)
    
    def setup_shortcuts(self):
        """Setup keyboard shortcuts for professional workflow"""
        from PySide6.QtGui import QShortcut, QKeySequence
        
        # Frame navigation
        QShortcut(QKeySequence(Qt.Key.Key_Left), self, 
                  lambda: self.comparison_viewer.previous_frame() if self.current_shot else None)
        QShortcut(QKeySequence(Qt.Key.Key_Right), self, 
                  lambda: self.comparison_viewer.next_frame() if self.current_shot else None)
        QShortcut(QKeySequence(Qt.Key.Key_Home), self, 
                  lambda: self.comparison_viewer.first_frame() if self.current_shot else None)
        QShortcut(QKeySequence(Qt.Key.Key_End), self, 
                  lambda: self.comparison_viewer.last_frame() if self.current_shot else None)
        
        # Shot navigation
        QShortcut(QKeySequence(Qt.Key.Key_Tab), self, self.next_shot)
        QShortcut(QKeySequence(Qt.SHIFT | Qt.Key.Key_Tab), self, self.previous_shot)
        QShortcut(QKeySequence(Qt.Key.Key_PageDown), self, self.next_shot)
        QShortcut(QKeySequence(Qt.Key.Key_PageUp), self, self.previous_shot)
        
        # Review actions
        QShortcut(QKeySequence(Qt.Key.Key_A), self, self.approve_shot)
        QShortcut(QKeySequence(Qt.Key.Key_R), self, self.reject_shot)
        QShortcut(QKeySequence(Qt.Key.Key_N), self, self.add_note)
        
        # Continuity check
        QShortcut(QKeySequence(Qt.Key.Key_C), self, self.check_continuity)
        
        # Batch operations
        QShortcut(QKeySequence(Qt.CTRL | Qt.Key.Key_A), self, self.select_all_shots)
        
        # View controls
        QShortcut(QKeySequence(Qt.Key.Key_F), self, self.toggle_fullscreen_viewer)
        
        # Help
        QShortcut(QKeySequence(Qt.Key.Key_Question), self, self.show_shortcuts_help)
        QShortcut(QKeySequence(Qt.Key.Key_H), self, self.show_shortcuts_help)
        
        logger.info("Keyboard shortcuts initialized")
    
    def toggle_fullscreen_viewer(self):
        """Toggle fullscreen mode for comparison viewer"""
        logger.info("Fullscreen toggle (coming soon)")
    
    def check_continuity(self):
        """Check continuity between current and next shot"""
        if not self.current_shot:
            QMessageBox.information(self, "No Shot", "Select a shot first")
            return
        
        # Get next shot
        current_row = self.shot_list.currentRow()
        if current_row >= self.shot_list.count() - 1:
            QMessageBox.information(self, "Last Shot", "This is the last shot")
            return
        
        next_shot = self.shots[current_row + 1]
        
        # Run continuity check
        from PySide6.QtWidgets import QProgressDialog
        
        progress = QProgressDialog("Analyzing continuity...", "Cancel", 0, 100, self)
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setValue(50)
        
        results = self.continuity_checker.check_all(self.current_shot, next_shot)
        
        progress.setValue(100)
        progress.close()
        
        # Display results
        self.show_continuity_results(self.current_shot, next_shot, results)
    
    def show_continuity_results(self, shot_a, shot_b, results):
        """Display continuity check results"""
        from PySide6.QtWidgets import QDialog, QVBoxLayout, QTextBrowser, QPushButton
        
        dialog = QDialog(self)
        dialog.setWindowTitle("Continuity Check Results")
        dialog.setMinimumSize(500, 400)
        
        layout = QVBoxLayout(dialog)
        
        # Create results HTML
        icon = self.continuity_checker.get_status_icon(results['overall'])
        
        html = f"""
        <h2>{icon} Continuity Check: {shot_a.name} -> {shot_b.name}</h2>
        
        <h3>Overall: {results['overall']}</h3>
        
        <table border="1" cellpadding="5" cellspacing="0" style="border-collapse: collapse; width: 100%;">
        <tr>
            <th>Check</th>
            <th>Result</th>
        </tr>
        <tr>
            <td><b>Motion Match</b></td>
            <td>{self.continuity_checker.get_status_icon(results['motion'])} {results['motion']}</td>
        </tr>
        <tr>
            <td><b>Color Consistency</b></td>
            <td>{self.continuity_checker.get_status_icon(results['color'])} {results['color']}</td>
        </tr>
        <tr>
            <td><b>Scale Match</b></td>
            <td>{self.continuity_checker.get_status_icon(results['scale'])} {results['scale']}</td>
        </tr>
        <tr>
            <td><b>Direction Flow</b></td>
            <td>{self.continuity_checker.get_status_icon(results['direction'])} {results['direction']}</td>
        </tr>
        </table>
        
        <p style="margin-top: 20px; color: #87857F; font-style: italic;">
        Tip: Use continuity check to ensure smooth shot-to-shot transitions
        </p>
        """
        
        browser = QTextBrowser()
        browser.setHtml(html)
        layout.addWidget(browser)
        
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(dialog.accept)
        layout.addWidget(close_btn)
        
        dialog.exec()
    
    def select_all_shots(self):
        """Select all shots for batch operations"""
        self.shot_list.selectAll()
        logger.info("Selected all shots")
    
    def batch_approve(self):
        """Approve all selected shots"""
        selected = self.shot_list.selectedItems()
        
        if not selected:
            QMessageBox.information(self, "No Selection", "Select shots to approve")
            return
        
        # Confirm
        reply = QMessageBox.question(
            self,
            "Batch Approve",
            f"Approve {len(selected)} shot(s)?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        
        if reply == QMessageBox.StandardButton.Yes:
            approved_shots = self.batch_service.batch_set_status(
                selected,
                self.shot_list,
                self.shots,
                self.dashboard_sync,
                ShotStatus.APPROVED,
            )
            
            self.update_shot_list()
            self.update_stats()

            for shot in approved_shots:
                self.start_proxy_render(shot)
            
            QMessageBox.information(self, "Success", f"Approved {len(selected)} shot(s)")
            logger.info(f"Batch approved {len(selected)} shots")
    
    def batch_reject(self):
        """Reject all selected shots"""
        selected = self.shot_list.selectedItems()
        
        if not selected:
            QMessageBox.information(self, "No Selection", "Select shots to reject")
            return
        
        # Confirm
        reply = QMessageBox.question(
            self,
            "Batch Reject",
            f"Reject {len(selected)} shot(s)?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        
        if reply == QMessageBox.StandardButton.Yes:
            self.batch_service.batch_set_status(
                selected,
                self.shot_list,
                self.shots,
                self.dashboard_sync,
                ShotStatus.REJECTED,
            )
            
            self.update_shot_list()
            self.update_stats()
            
            QMessageBox.information(self, "Success", f"Rejected {len(selected)} shot(s)")
            logger.info(f"Batch rejected {len(selected)} shots")
    
    def show_shortcuts_help(self):
        """Display keyboard shortcuts help dialog"""
        from PySide6.QtWidgets import QDialog, QVBoxLayout, QTextBrowser, QPushButton
        
        dialog = QDialog(self)
        dialog.setWindowTitle("Keyboard Shortcuts")
        dialog.setMinimumSize(500, 600)
        
        layout = QVBoxLayout(dialog)
        
        # Create help text
        help_text = """
        <h2>Keyboard Shortcuts</h2>
        
        <h3>Frame Navigation</h3>
        <table border="1" cellpadding="5" cellspacing="0" style="border-collapse: collapse; width: 100%;">
        <tr><td><b>Left/Right</b></td><td>Previous/Next frame</td></tr>
        <tr><td><b>Home/End</b></td><td>First/Last frame</td></tr>
        </table>
        
        <h3>Shot Navigation</h3>
        <table border="1" cellpadding="5" cellspacing="0" style="border-collapse: collapse; width: 100%;">
        <tr><td><b>Tab</b></td><td>Next shot</td></tr>
        <tr><td><b>Shift+Tab</b></td><td>Previous shot</td></tr>
        <tr><td><b>Page Down/Up</b></td><td>Next/Previous shot</td></tr>
        </table>
        
        <h3>Review Actions</h3>
        <table border="1" cellpadding="5" cellspacing="0" style="border-collapse: collapse; width: 100%;">
        <tr><td><b>A</b></td><td>Approve shot</td></tr>
        <tr><td><b>R</b></td><td>Reject shot</td></tr>
        <tr><td><b>N</b></td><td>Add note</td></tr>
        </table>
        
        <h3>View Controls</h3>
        <table border="1" cellpadding="5" cellspacing="0" style="border-collapse: collapse; width: 100%;">
        <tr><td><b>F</b></td><td>Toggle fullscreen (coming soon)</td></tr>
        </table>
        
        <h3>Help</h3>
        <table border="1" cellpadding="5" cellspacing="0" style="border-collapse: collapse; width: 100%;">
        <tr><td><b>H or ?</b></td><td>Show this help</td></tr>
        </table>
        
        <p style="margin-top: 20px; color: #87857F; font-style: italic;">
        Tip: Use keyboard shortcuts for faster review workflow!
        </p>
        """
        
        browser = QTextBrowser()
        browser.setHtml(help_text)
        layout.addWidget(browser)
        
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(dialog.accept)
        layout.addWidget(close_btn)
        
        dialog.exec()


    # === PAGINATION METHODS ===
    
    def next_page(self):
        return next_page(self)
    
    def prev_page(self):
        return prev_page(self)
    
    # === CACHE MONITORING METHODS ===
    
    def update_cache_stats(self):
        return update_cache_stats(self)
    
    def clear_all_cache(self):
        """Clear all cached frames"""
        if hasattr(self, 'comparison_viewer') and hasattr(self.comparison_viewer, 'cache'):
            self.comparison_viewer.cache.clear()
            self.update_cache_stats()
            QMessageBox.information(self, "Cache Cleared", "All cached frames have been cleared.")
            logger.info("Cache cleared by user")

    def cleanup_resources(self):
        """Clean up all resources, workers, and timers; Stops all active threads."""
        if self._is_cleaned:
            return

        self._is_closing = True
        logger.info("Cleaning up ShotReviewTab resources...")
        
        # Stop Cache Monitor
        if hasattr(self, 'cache_monitor_timer') and self.cache_monitor_timer.isActive():
            self.cache_monitor_timer.stop()
            
        # Stop background workers
        if hasattr(self, "project_service"):
            self.project_service.cleanup(timeout_ms=2000)
        self._stop_thread_worker("pull_worker")
        self._stop_thread_worker("proxy_render_worker")
        self._stop_thread_worker("_cache_worker")

        self.proxy_render_queue = []
        if self.proxy_progress_dialog:
            self.proxy_progress_dialog.close()
            self.proxy_progress_dialog = None
        if hasattr(self, '_cache_progress') and self._cache_progress:
            self._cache_progress.close()
            self._cache_progress = None
            
        # Cleanup Viewer
        if hasattr(self, 'comparison_viewer'):
            self.comparison_viewer.cleanup()
            self.comparison_viewer.close()
            
        self._is_cleaned = True
        logger.info("ShotReviewTab cleanup complete")

    def closeEvent(self, event):
        """Handle widget closure"""
        self.cleanup_resources()
        super().closeEvent(event)
