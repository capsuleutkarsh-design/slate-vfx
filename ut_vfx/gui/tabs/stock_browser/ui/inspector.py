from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QLabel, QGridLayout, 
    QSizePolicy
)
from PySide6.QtCore import Qt, Signal
import json
import logging
import os
from pathlib import Path

# Internal imports
# ....widgets -> gui/widgets
from ....widgets.advanced_player import AdvancedPlayer
from ut_vfx.utils.media_capabilities import is_video, is_image
from ....widgets.styled_buttons import PrimaryButton
# .....core -> ut_vfx/core
from .....core.infra.design_tokens import ColorTokens as C, TypographyTokens as T

class StockInspectorPanel(QWidget):
    """
    Inspector Panel for Stock Browser.
    Displays:
    - AdvancedPlayer (Preview)
    - Metadata (Resolution, FPS, Duration)
    - Tags
    """
    next_requested = Signal()
    prev_requested = Signal()
    analysis_requested = Signal(str, dict) # path, asset_dict

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setup_ui()
        
    def setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.setMinimumWidth(280)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)
        
        # 1. Player
        self.player = AdvancedPlayer()
        self.player.setMinimumHeight(280)
        self.player.setMaximumHeight(600)
        self.player.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        # Connect Navigation Signals
        self.player.next_requested.connect(self.next_requested.emit)
        self.player.prev_requested.connect(self.prev_requested.emit)
        
        layout.addWidget(self.player, 2)
        
        # 2. Metadata Group
        self.meta_group = QWidget()
        meta_layout = QVBoxLayout(self.meta_group)
        meta_layout.setContentsMargins(12, 14, 12, 12)
        meta_layout.setSpacing(14)

        # Title / Name (Hero)
        self.lbl_name = QLabel("-")
        self.lbl_name.setStyleSheet(f"font-size: 16px; font-weight: {T.WEIGHT_STYLE_BOLD}; color: white;")
        self.lbl_name.setWordWrap(True)
        meta_layout.addWidget(self.lbl_name)

        # Tech Specs Grid (Badges)
        tech_widget = QWidget()
        tech_widget.setStyleSheet(f"background-color: {C.BG_SIDEBAR}; border-radius: 8px;")
        tech_layout = QGridLayout(tech_widget)
        tech_layout.setContentsMargins(12, 12, 12, 12)
        tech_layout.setVerticalSpacing(6)
        tech_layout.setHorizontalSpacing(20)

        # Labels (Dim)
        lbl_res_t = QLabel("RESOLUTION"); lbl_res_t.setStyleSheet(f"color: {C.TEXT_SECONDARY}; font-size: 11px; font-weight: bold; letter-spacing: 1px;")
        lbl_fps_t = QLabel("FPS"); lbl_fps_t.setStyleSheet(f"color: {C.TEXT_SECONDARY}; font-size: 11px; font-weight: bold; letter-spacing: 1px;") 
        lbl_dur_t = QLabel("DURATION"); lbl_dur_t.setStyleSheet(f"color: {C.TEXT_SECONDARY}; font-size: 11px; font-weight: bold; letter-spacing: 1px;")

        # Values (Bright)
        self.lbl_res = QLabel("-"); self.lbl_res.setStyleSheet(f"color: {C.ACCENT_PRIMARY}; font-weight: bold; font-size: 14px;")
        self.lbl_fps = QLabel("-"); self.lbl_fps.setStyleSheet(f"color: {C.TEXT_PRIMARY}; font-size: 13px;")
        self.lbl_dur = QLabel("-"); self.lbl_dur.setStyleSheet(f"color: {C.TEXT_PRIMARY}; font-size: 13px;")

        tech_layout.addWidget(lbl_res_t, 0, 0); tech_layout.addWidget(self.lbl_res, 1, 0)
        tech_layout.addWidget(lbl_dur_t, 0, 1); tech_layout.addWidget(self.lbl_dur, 1, 1)
        tech_layout.addWidget(lbl_fps_t, 0, 2); tech_layout.addWidget(self.lbl_fps, 1, 2)
        
        meta_layout.addWidget(tech_widget)

        # Tags Section
        tags_container = QWidget()
        tags_layout = QVBoxLayout(tags_container)
        tags_layout.setContentsMargins(0,0,0,0)
        tags_layout.setSpacing(6)
        
        lbl_tags_t = QLabel("TAGS")
        lbl_tags_t.setStyleSheet(f"color: {C.TEXT_SECONDARY}; font-size: 11px; font-weight: bold; letter-spacing: 1px;")
        
        self.lbl_tags = QLabel("-")
        self.lbl_tags.setWordWrap(True)
        self.lbl_tags.setStyleSheet(f"color: {C.TEXT_PRIMARY}; font-size: 12px; line-height: 1.4;")
        self.lbl_tags.setTextInteractionFlags(Qt.TextSelectableByMouse)
        
        tags_layout.addWidget(lbl_tags_t)
        tags_layout.addWidget(self.lbl_tags)
        
        meta_layout.addWidget(tags_container)
        
        meta_layout.addStretch() 
        
        layout.addWidget(self.meta_group, 1)
        self.set_compact_mode(False)

    def set_visible(self, visible):
        super().setVisible(visible)

    def update_asset(self, asset):
        self.current_asset = asset
        if not asset: 
            return 
            
        try:
            real_path = asset.get('path') or asset.get('file_path')
            proxy_path = asset.get('proxy_path')
            tags = asset.get('tags', [])
            meta = json.loads(asset.get('metadata', '{}')) if isinstance(asset.get('metadata'), str) else asset.get('metadata', {})
            
            status = asset.get('status', 'ready')
            is_pending = (status == 'pending' or status == 'ingesting')
            
            fallback_name = Path(real_path).name if real_path else "Unknown"
            self.lbl_name.setText(asset.get('name', '-') or fallback_name)
            
            tag_text = ", ".join(tags) if isinstance(tags, list) else str(tags)
            self.lbl_tags.setText(tag_text if tag_text else "-")
            
            # Check if metadata has real values (not just defaults of 0)
            has_resolution = meta.get('width') not in (None, 0, '') and meta.get('height') not in (None, 0, '')
            has_duration = meta.get('duration_sec') not in (None, 0, 0.0, '') or meta.get('duration') not in (None, 0, 0.0, '')
            has_real_metadata = has_resolution or has_duration
            
            if is_pending:
                self.lbl_res.setText("Analyzing...")
                self.lbl_res.setStyleSheet(f"color: {C.WARNING}; font-weight: bold;")
                self.lbl_fps.setText("-")
                self.lbl_dur.setText("-")
            elif has_real_metadata:
                w = meta.get('width') or meta.get('res_w', 0)
                h = meta.get('height') or meta.get('res_h', 0)
                fps = meta.get('fps', 0.0)
                dur = meta.get('duration_sec') or meta.get('duration', 0)
                
                if w and h:
                    self.lbl_res.setText(f"{w} x {h}")
                    self.lbl_res.setStyleSheet(f"color: {C.ACCENT_PRIMARY}; font-weight: bold;")
                else:
                    self.lbl_res.setText("N/A")
                    self.lbl_res.setStyleSheet(f"color: {C.TEXT_SECONDARY};")
                    
                self.lbl_fps.setText(f"{float(fps):.2f}" if fps else "-")
                self.lbl_dur.setText(f"{int(dur//60)}:{int(dur%60):02d}" if dur else "-")
            else:
                # No metadata yet — show N/A and trigger background analysis if file exists
                self.lbl_res.setText("N/A")
                self.lbl_res.setStyleSheet(f"color: {C.TEXT_SECONDARY};")
                self.lbl_fps.setText("-")
                self.lbl_dur.setText("-")
                
                if real_path and os.path.exists(real_path):
                    asset['status'] = 'ingesting' 
                    self.analysis_requested.emit(real_path, asset)
            
            target_video = proxy_path if (proxy_path and os.path.exists(proxy_path)) else real_path
            
            is_proxy = (target_video == proxy_path) and (target_video is not None)

            if target_video:
                 ext = Path(str(target_video)).suffix.lower()
                 if is_video(ext) or is_image(ext):
                     self.player.load(target_video)
                 else:
                     self.player.stop_media()
                     self.player.screen.set_text("No Preview")
            else:
                 self.player.stop_media()
                 self.player.screen.set_text("No Preview")
                 
        except Exception as e:
            import logging
            logging.exception(f"Error updating inspector: {e}")
            self.player.screen.set_text(f"Error: {e}")

    def cleanup(self):
        if self.player:
            self.player.close()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.set_compact_mode(self.width() < 320)

    def set_compact_mode(self, compact: bool):
        """Reduce vertical pressure for narrow inspector widths."""
        if compact:
            self.player.setMinimumHeight(220)
            self.player.setMaximumHeight(400)
            self.lbl_name.setStyleSheet(f"font-size: 14px; font-weight: {T.WEIGHT_STYLE_BOLD}; color: white;")
        else:
            self.player.setMinimumHeight(280)
            self.player.setMaximumHeight(600)
            self.lbl_name.setStyleSheet(f"font-size: 16px; font-weight: {T.WEIGHT_STYLE_BOLD}; color: white;")
