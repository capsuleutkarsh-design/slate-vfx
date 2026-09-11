"""
Export Dialog - Timeline & Video Export

Professional export dialog for lineup export with multiple format options.
"""

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QComboBox, QCheckBox, QLineEdit,
    QFileDialog, QGroupBox, QFormLayout, QProgressBar,
    QTextBrowser, QMessageBox
)
from PySide6.QtCore import Signal
from pathlib import Path
import logging

logger = logging.getLogger(__name__)

_HAS_OTIO = False


class ExportManager:
    """Lightweight wrapper to provide export info from shot list."""
    def __init__(self, shots, fps=24.0):
        self.shots = shots
        self.fps = fps
    
    def get_shot_count(self):
        return len(self.shots)
    
    def get_duration(self):
        total_frames = sum(s.get_frame_count() or 0 for s in self.shots)
        return total_frames / self.fps if self.fps else 0


class ExportDialog(QDialog):
    """
    Export dialog for timeline and video export
    
    Supports multiple formats and export options.
    """
    
    export_started = Signal()
    export_completed = Signal(bool, str)
    
    def __init__(self, shots, parent=None, initial_format: str = None, fps=24.0):
        super().__init__(parent)
        
        self.manager = ExportManager(shots, fps)  # Lightweight wrapper
        self.output_path = None
        self.initial_format = initial_format
        
        self.setWindowTitle("Export Lineup")
        self.setMinimumSize(600, 700)
        
        self.setup_ui()
        
    def setup_ui(self):
        """Create UI"""
        layout = QVBoxLayout(self)
        
        title = QLabel("EXPORT LINEUP")
        title.setStyleSheet("font-size: 16px; font-weight: bold; padding: 10px;")
        layout.addWidget(title)
        
        info = QLabel(f"Timeline: {self.manager.get_shot_count()} shots, {self.manager.get_duration():.2f}s")
        info.setStyleSheet("color: #87857F; padding: 5px;")
        layout.addWidget(info)
        

        
        # Export format group
        format_group = QGroupBox("Export Format")
        format_layout = QVBoxLayout(format_group)

        self.format_combo = QComboBox()
        formats = [
            "MP4 Video (Side-by-Side)",
            "EDL (Edit Decision List)",
            "XML (Premiere Pro)",
            "XML (Final Cut Pro)",
        ]
        self.format_combo.addItems(formats)
        self.format_combo.currentTextChanged.connect(self.on_format_changed)
        format_layout.addWidget(self.format_combo)
        
        layout.addWidget(format_group)
        
        # Video options (shown for MP4)
        self.video_group = QGroupBox("Video Options")
        video_layout = QFormLayout(self.video_group)
        
        self.preset_combo = QComboBox()
        self.preset_combo.addItems([
            "Client Review (1080p, 10Mbps)",
            "High Quality (ProRes)",
            "Web Preview (720p, 5Mbps)"
        ])
        video_layout.addRow("Preset:", self.preset_combo)
        
        self.layout_combo = QComboBox()
        self.layout_combo.addItems([
            "Side-by-Side",
            "Over/Under",
            "Render Only",
            "Scan Only"
        ])
        video_layout.addRow("Layout:", self.layout_combo)
        
        layout.addWidget(self.video_group)
        
        # Burnin options
        self.burnin_group = QGroupBox("Burnin Options")
        burnin_layout = QVBoxLayout(self.burnin_group)
        
        self.burnin_shot_name = QCheckBox("Shot Name")
        self.burnin_shot_name.setChecked(True)
        burnin_layout.addWidget(self.burnin_shot_name)
        
        self.burnin_frame_counter = QCheckBox("Frame Counter")
        self.burnin_frame_counter.setChecked(True)
        burnin_layout.addWidget(self.burnin_frame_counter)
        
        self.burnin_timecode = QCheckBox("Timecode")
        burnin_layout.addWidget(self.burnin_timecode)
        
        self.burnin_status = QCheckBox("Status Watermark (APPROVED)")
        burnin_layout.addWidget(self.burnin_status)
        
        layout.addWidget(self.burnin_group)
        
        # Output path
        output_group = QGroupBox("Output File")
        output_layout = QHBoxLayout(output_group)
        
        self.output_line = QLineEdit()
        self.output_line.setPlaceholderText("Select output file...")
        output_layout.addWidget(self.output_line)
        
        browse_btn = QPushButton("Browse...")
        browse_btn.clicked.connect(self.browse_output)
        output_layout.addWidget(browse_btn)
        
        layout.addWidget(output_group)
        
        # Progress bar
        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        layout.addWidget(self.progress_bar)
        
        # Status text
        self.status_browser = QTextBrowser()
        self.status_browser.setMaximumHeight(150)
        self.status_browser.setVisible(False)
        layout.addWidget(self.status_browser)
        
        # Buttons
        btn_layout = QHBoxLayout()

        self.export_btn = QPushButton("Export")
        self.export_btn.clicked.connect(self.start_export)
        self.export_btn.setStyleSheet("padding: 10px; font-weight: bold;")
        btn_layout.addWidget(self.export_btn)



        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        btn_layout.addWidget(cancel_btn)

        layout.addLayout(btn_layout)
        
        # Initial state
        if self.initial_format:
            for i in range(self.format_combo.count()):
                text = self.format_combo.itemText(i)
                if self.initial_format.lower() in text.lower():
                    self.format_combo.setCurrentIndex(i)
                    break 
        self.on_format_changed(self.format_combo.currentText())

    def on_format_changed(self, format_text):
        """Show/hide options based on selected format"""
        is_video = "MP4" in format_text
        self.video_group.setVisible(is_video)
        self.burnin_group.setVisible(is_video)
    
    def browse_output(self):
        """Browse for output file location"""
        format_text = self.format_combo.currentText()
        
        if "MP4" in format_text:
            filter_str = "Video Files (*.mp4)"
            default_name = "lineup_export.mp4"
        elif "EDL" in format_text:
            filter_str = "EDL Files (*.edl)"
            default_name = "lineup_export.edl"
        elif "OTIO" in format_text:
            filter_str = "OTIO Files (*.otio)"
            default_name = "lineup_export.otio"
        elif "AAF" in format_text:
            filter_str = "AAF Files (*.aaf)"
            default_name = "lineup_export.aaf"
        else:
            filter_str = "XML Files (*.xml)"
            default_name = "lineup_export.xml"
        
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Export As", default_name, filter_str
        )
        if path:
            self.output_line.setText(path)
            self.output_path = Path(path)
    
    def start_export(self):
        """Validate and start the export process"""
        # Ensure output path is set
        if not self.output_path:
            line_text = self.output_line.text().strip()
            if line_text:
                self.output_path = Path(line_text)
            else:
                QMessageBox.warning(self, "No Output", "Please select an output file first.")
                return
        
        self.export_btn.setEnabled(False)
        self.progress_bar.setVisible(True)
        self.status_browser.setVisible(True)
        self.progress_bar.setValue(0)
        
        self.export_started.emit()
        
        success = self.do_export()
        
        self.progress_bar.setValue(100)
        self.export_btn.setEnabled(True)
        
        if success:
            self.log_status("\n✅ Export complete!")
            self.export_completed.emit(True, str(self.output_path))
            QMessageBox.information(self, "Export Complete", f"Exported successfully to:\n{self.output_path}")
        else:
            self.log_status("\n❌ Export failed.")
            self.export_completed.emit(False, "")

    def do_export(self) -> bool:
        """Perform the export using the selected exporter."""
        from ....core.domain.video_exporter import VideoExporter
        from ....core.domain.edl_exporter import export_edl, export_edl_dual_track
        from ....core.domain.xml_exporter import export_xml
        
        format_name = self.format_combo.currentText()
        
        try:
            self.log_status(f"Exporting: {format_name}")
            self.progress_bar.setValue(20)
            
            # Get shots from parent tab
            shots_to_export = self.get_shots_from_parent()
            
            if not shots_to_export:
                self.log_status("Error: No shots available to export")
                return False
            
            self.log_status(f"Found {len(shots_to_export)} shots to export")
            self.progress_bar.setValue(30)
            
            if "MP4" in format_name:
                self.log_status("Exporting video...")
                
                config_manager = getattr(self.parent(), 'config', None)
                exporter = VideoExporter(config_manager)
                
                burnin_config = {
                    'shot_name': self.burnin_shot_name.isChecked(),
                    'frame_counter': self.burnin_frame_counter.isChecked(),
                    'timecode': self.burnin_timecode.isChecked(),
                    'status': self.burnin_status.isChecked()
                }
                
                preset_map = {
                    "Client Review (1080p, 10Mbps)": "client_review",
                    "High Quality (ProRes)": "high_quality",
                    "Web Preview (720p, 5Mbps)": "web_preview"
                }
                preset = preset_map.get(self.preset_combo.currentText(), 'client_review')
                
                self.progress_bar.setValue(40)
                
                # Export using VideoExporter
                success = exporter.export_comparison_video(
                    shots=shots_to_export,
                    output_path=self.output_path,
                    preset=preset,
                    layout=self.layout_combo.currentText(),
                    burnin_config=burnin_config
                )
                
                self.progress_bar.setValue(100)
                return success
                
            elif "EDL" in format_name:
                self.log_status("Generating EDL...")
                self.progress_bar.setValue(50)
                
                # Use dual-track EDL if both scan and render exist
                has_scan = all(hasattr(s, 'scan_path') and s.scan_path for s in shots_to_export)
                
                if has_scan:
                    success = export_edl_dual_track(shots_to_export, self.output_path, fps=self.manager.fps)
                else:
                    success = export_edl(shots_to_export, self.output_path, fps=self.manager.fps)
                
                self.progress_bar.setValue(100)
                
                if success:
                    self.log_status(f"EDL exported: {self.output_path}")
                return success
            
            elif "Premiere" in format_name:
                self.log_status("Generating Premiere XML...")
                self.progress_bar.setValue(50)
                
                success = export_xml(shots_to_export, self.output_path, 'premiere', fps=self.manager.fps)
                self.progress_bar.setValue(100)
                
                if success:
                    self.log_status(f"Premiere XML exported: {self.output_path}")
                return success
            
            elif "Final Cut" in format_name:
                self.log_status("Generating Final Cut XML...")
                self.progress_bar.setValue(50)

                success = export_xml(shots_to_export, self.output_path, 'fcpxml', fps=self.manager.fps)
                self.progress_bar.setValue(100)

                if success:
                    self.log_status(f"Final Cut XML exported: {self.output_path}")
                return success



            return False
            
        except Exception as e:
            self.log_status(f"Error: {str(e)}")
            logger.error(f"Export error: {e}", exc_info=True)
            return False
    
    def get_shots_from_parent(self):
        """Get shots list from parent tab."""
        try:
            parent_tab = self.parent()
            
            # Try to get shots from parent's lineup or shot list
            if hasattr(parent_tab, 'lineup_shots'):
                return parent_tab.lineup_shots
            elif hasattr(parent_tab, 'shots'):
                return parent_tab.shots
            elif hasattr(parent_tab, 'get_current_shots'):
                return parent_tab.get_current_shots()
            else:
                logger.warning("Could not find shots in parent tab")
                return []
                
        except Exception as e:
            logger.error(f"Error getting shots from parent: {e}")
            return []
    
    def log_status(self, message: str):
        """Add status message"""
        self.status_browser.append(message)
        logger.info(message)


