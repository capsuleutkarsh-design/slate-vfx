import logging
from PySide6.QtWidgets import *
from PySide6.QtCore import *
from PySide6.QtGui import *

logger = logging.getLogger(__name__)
from ..comparison_viewer import ViewMode
from ut_vfx.gui.tabs.shot_review.image_viewer import ImageViewer
from ut_vfx.gui.widgets.nuke_slider import NukeSlider
try:
    from ut_vfx.core.domain.color_manager import ColorManager
    _HAS_OCIO = True
except ImportError:
    _HAS_OCIO = False

class ViewerUIMixin:

    def setup_ui(self):
            """Create UI layout"""
            layout = QVBoxLayout(self)
            layout.setContentsMargins(0, 0, 0, 0)

            # Title bar
            title_layout = QHBoxLayout()

            title = QLabel("\U0001F441\uFE0F COMPARISON VIEWER")
            title.setStyleSheet("font-size: 14px; font-weight: bold; padding: 5px;")
            title_layout.addWidget(title)

            # Initialize hidden frame info to prevent crashes
            self.frame_info = QLabel()
            self.frame_info.hide()


            title_layout.addStretch()

            # OCIO Colorspace dropdown (only if OpenColorIO is installed)
            self._ocio_combo = None
            if _HAS_OCIO:
                try:
                    cm = ColorManager.instance()
                    if cm.is_available():
                        ocio_label = QLabel("OCIO:")
                        ocio_label.setStyleSheet("color: #87857F; font-size: 11px; padding: 2px;")
                        title_layout.addWidget(ocio_label)

                        self._ocio_combo = QComboBox()
                        self._ocio_combo.setFixedWidth(180)
                        self._ocio_combo.setStyleSheet("""
                            QComboBox {
                                background: rgba(255, 255, 255, 0.05); color: #E8E6E1;
                                border: 1px solid rgba(255, 255, 255, 0.1); border-radius: 6px;
                                padding: 4px 8px; font-size: 11px;
                            }
                            QComboBox::drop-down { border: none; width: 20px; }
                            QComboBox::down-arrow { image: none; }
                            QComboBox:hover { background: rgba(255, 255, 255, 0.1); border: 1px solid rgba(255, 255, 255, 0.2); }
                            QComboBox QAbstractItemView {
                                background: #1D1D22; color: #E8E6E1;
                                selection-background-color: #3EA8BF;
                                border: 1px solid #26262D;
                                border-radius: 4px;
                            }
                        """)
                        self._ocio_combo.addItem("Raw (No Transform)", None)
                        for cs_name, cs_label in cm.get_common_colorspaces():
                            self._ocio_combo.addItem(cs_label, cs_name)
                        self._ocio_combo.currentIndexChanged.connect(self._on_colorspace_changed)
                        title_layout.addWidget(self._ocio_combo)
                except Exception as e:
                    logger.warning(f"OCIO UI setup failed: {e}")

            # View mode (future enhancement)
            self.view_mode_label = QLabel("Mode: Side-by-Side")
            self.view_mode_label.setStyleSheet("color: #87857F; padding: 5px;")
            title_layout.addWidget(self.view_mode_label)

            layout.addLayout(title_layout)

            # Dual viewers (side-by-side)
            viewers_layout = QHBoxLayout()
            viewers_layout.setSpacing(5)

            # Left: Scan
            self.scan_viewer = ImageViewer("SCAN (Plate)")
            self.scan_viewer.set_hud_enabled(False)
            viewers_layout.addWidget(self.scan_viewer, 1)

            # Right: Render (Container for Image/Video switch)
            self.render_container = QWidget()
            self.render_layout = QVBoxLayout(self.render_container)
            self.render_layout.setContentsMargins(0, 0, 0, 0)

            self.render_viewer = ImageViewer("RENDER (Comp)")
            self.render_viewer.set_hud_enabled(False)
            self.render_layout.addWidget(self.render_viewer)

            # from .video_player_widget import VideoPlayerWidget
            from ut_vfx.gui.widgets.advanced_player import AdvancedPlayer
            self.video_player = AdvancedPlayer()
            self.video_player.set_controls_visible(False) # Embedded mode
            self.video_player.hide()
            self.render_layout.addWidget(self.video_player)

            viewers_layout.addWidget(self.render_container, 1)


            # --- VIEWERS ---
            layout.addLayout(viewers_layout, 1)  # Stretch factor for viewers

            # --- CONTROLS CONTAINER ---
            # Unified bottom panel
            controls_container = QWidget()
            controls_container.setStyleSheet("""
                QWidget { background: rgba(0, 0, 0, 0.6); border-top: 1px solid rgba(255, 255, 255, 0.05); }
                QLabel { color: #B4B1AA; }
            """)
            controls_layout = QVBoxLayout(controls_container)
            controls_layout.setContentsMargins(10, 5, 10, 5)
            controls_layout.setSpacing(5)

            # 1. Scrubber Row (Full Width)
            scrubber_layout = QHBoxLayout()
            self.frame_slider = NukeSlider()
            self.frame_slider.setEnabled(False)
            self.frame_slider.valueChanged.connect(self.on_slider_changed)
            scrubber_layout.addWidget(self.frame_slider)
            controls_layout.addLayout(scrubber_layout)

            # 2. Tools Row (Grading | Nav | Modes)
            tools_row = QHBoxLayout()
            tools_row.setContentsMargins(0, 0, 0, 0)
            tools_row.setSpacing(20)

            # A. Grading Group (Left)
            grading_group = QFrame()
            grading_group.setStyleSheet("""
                QFrame { background: rgba(0, 0, 0, 0.4); border-radius: 12px; border: 1px solid rgba(255, 255, 255, 0.05); }
                QLabel { background: transparent; border: none; }
            """)
            grading_layout = QHBoxLayout(grading_group)
            grading_layout.setContentsMargins(12, 6, 12, 6)
            grading_layout.setSpacing(15)

            # Helper for mini sliders
            def add_mini_slider(lbl_txt, min_v, max_v, def_v, cb, attr):
                vbox = QVBoxLayout()
                vbox.setSpacing(0)

                hbox = QHBoxLayout()
                l = QLabel(lbl_txt)
                l.setStyleSheet("font-size: 10px; font-weight: bold; color: #87857F;")
                val = QLabel(f"{def_v/100:.2f}")
                val.setStyleSheet("font-size: 10px; color: #3EA8BF;")
                hbox.addWidget(l)
                hbox.addStretch()
                hbox.addWidget(val)
                vbox.addLayout(hbox)

                sl = QSlider(Qt.Orientation.Horizontal)
                sl.setRange(min_v, max_v)
                sl.setValue(def_v)
                sl.setFixedWidth(80) # Compact width
                sl.valueChanged.connect(cb)
                sl.valueChanged.connect(lambda v: val.setText(f"{v/100:.2f}"))
                sl.sliderPressed.connect(self.start_interactive_grade)
                sl.sliderReleased.connect(self.stop_interactive_grade)

                vbox.addWidget(sl)
                grading_layout.addLayout(vbox)

                setattr(self, f"{attr}_slider", sl)
                setattr(self, f"{attr}_val_label", val)

            add_mini_slider("GAIN", 0, 400, 100, self.on_grading_changed, "gain")
            add_mini_slider("GAMMA", 1, 400, 100, self.on_grading_changed, "gamma")
            add_mini_slider("SAT", 0, 200, 100, self.on_grading_changed, "sat")

            btn_reset = QPushButton("\u21BA")
            btn_reset.setMinimumSize(24, 24)
            btn_reset.setToolTip("Reset Color")
            btn_reset.clicked.connect(self.reset_grading)
            grading_layout.addWidget(btn_reset)

            tools_row.addWidget(grading_group)

            tools_row.addStretch() # Spacer

            # B. Navigation Group (Center)
            nav_frame = QFrame()
            nav_frame.setStyleSheet("""
                QFrame { background: rgba(0, 0, 0, 0.4); border-radius: 12px; border: 1px solid rgba(255, 255, 255, 0.05); }
                QPushButton { background: transparent; border: none; border-radius: 4px; }
                QPushButton:hover { background: rgba(255, 255, 255, 0.1); }
            """)
            nav_group = QHBoxLayout(nav_frame)
            nav_group.setContentsMargins(6, 2, 6, 2)
            nav_group.setSpacing(2)

            style_nav = "QPushButton { min-width: 30px; font-size: 14px; padding: 4px; }"
            self.btn_first = QPushButton("\u23EE")
            self.btn_first.setStyleSheet(style_nav)
            self.btn_first.clicked.connect(self.first_frame)
            self.btn_prev = QPushButton("\u25C0")
            self.btn_prev.setStyleSheet(style_nav)
            self.btn_prev.clicked.connect(self.previous_frame)

            self.btn_play = QPushButton("\u25B6")
            self.btn_play.setStyleSheet("QPushButton { min-width: 30px; font-size: 16px; padding: 4px; color: #3EA8BF; }")
            self.btn_play.clicked.connect(self.toggle_playback)
            self.btn_play.setEnabled(False)

            self.btn_next = QPushButton("\u25B6")
            self.btn_next.setStyleSheet(style_nav)
            self.btn_next.clicked.connect(self.next_frame)
            self.btn_last = QPushButton("\u23ED")
            self.btn_last.setStyleSheet(style_nav)
            self.btn_last.clicked.connect(self.last_frame)

            nav_group.addWidget(self.btn_first)
            nav_group.addWidget(self.btn_prev)
            nav_group.addWidget(self.btn_play)

            # self.frame_info = QLabel("-- / --")
            # self.frame_info.setFixedWidth(80)
            # self.frame_info.setAlignment(Qt.AlignmentFlag.AlignCenter)
            # self.frame_info.setStyleSheet("font-weight: bold; color: #E8E6E1;")
            # nav_group.addWidget(self.frame_info)

            nav_group.addWidget(self.btn_next)
            nav_group.addWidget(self.btn_last)

            tools_row.addWidget(nav_frame)

            tools_row.addStretch() # Spacer

            # C. HUD & View Modes
            right_group = QHBoxLayout()
            right_group.setSpacing(10)

            # HUD Toggle
            self.btn_hud = QPushButton("HUD")
            self.btn_hud.setCheckable(True)
            self.btn_hud.setChecked(False)  # Start hidden; appears on hover when enabled
            self.btn_hud.setMinimumSize(60, 28)
            self.btn_hud.setStyleSheet("""
                QPushButton {
                    font-size: 10px;
                    font-weight: bold;
                    padding: 2px;
                }
                QPushButton:checked {
                    background-color: #3EA8BF;
                    color: white;
                    border: 1px solid #6BA4C9;
                }
            """)
            self.btn_hud.clicked.connect(self.toggle_hud)
            self.btn_hud.setToolTip("Toggle Smart HUD Overlay (Frame info, resolution, format)")
            right_group.addWidget(self.btn_hud)

            # View Modes
            mode_layout = QHBoxLayout()
            mode_layout.setSpacing(1)

            self.btn_mode_dual = QPushButton("\u25A3 DUAL")
            self.btn_mode_dual.setCheckable(True)
            self.btn_mode_dual.setChecked(True)
            self.btn_mode_dual.setMinimumSize(50, 28)
            self.btn_mode_dual.setToolTip("Side-by-Side (0)")
            self.btn_mode_dual.clicked.connect(lambda: self.set_view_mode(ViewMode.SIDE_BY_SIDE))

            self.btn_mode_scan = QPushButton("SCAN")
            self.btn_mode_scan.setCheckable(True)
            self.btn_mode_scan.setMinimumSize(45, 28)
            self.btn_mode_scan.setToolTip("Scan Only (1)")
            self.btn_mode_scan.clicked.connect(lambda: self.set_view_mode(ViewMode.SCAN_ONLY))

            self.btn_mode_render = QPushButton("REND")
            self.btn_mode_render.setCheckable(True)
            self.btn_mode_render.setMinimumSize(45, 28)
            self.btn_mode_render.setToolTip("Render Only (2)")
            self.btn_mode_render.clicked.connect(lambda: self.set_view_mode(ViewMode.RENDER_ONLY))

            # Style
            for btn in [self.btn_mode_dual, self.btn_mode_scan, self.btn_mode_render]:
                btn.setStyleSheet("""
                    QPushButton { background: #26262D; color: #87857F; border: none; font-size: 10px; font-weight: bold; }
                    QPushButton:checked { background: #3EA8BF; color: white; }
                    QPushButton:hover { background: #2C2C34; }
                """)
                mode_layout.addWidget(btn)

            self.mode_group = QButtonGroup(self)
            self.mode_group.addButton(self.btn_mode_dual)
            self.mode_group.addButton(self.btn_mode_scan)
            self.mode_group.addButton(self.btn_mode_render)

            right_group.addLayout(mode_layout)

            # D. Annotations
            anno_layout = QHBoxLayout()
            anno_layout.setSpacing(1)

            self.btn_pen = QPushButton("\u270F\uFE0F")
            self.btn_pen.setCheckable(True)
            self.btn_pen.setMinimumSize(28, 28)
            self.btn_pen.setToolTip("Toggle Drawing (Wacom Supported)")
            self.btn_pen.clicked.connect(self.toggle_annotations)
            self.btn_pen.setStyleSheet("""
                QPushButton { background: #26262D; border: none; font-size: 14px; }
                QPushButton:checked { background: #D9635F; }
                QPushButton:hover { background: #2C2C34; }
            """)

            self.btn_clear_anno = QPushButton("\U0001F5D1\uFE0F")
            self.btn_clear_anno.setMinimumSize(28, 28)
            self.btn_clear_anno.setToolTip("Clear Annotations")
            self.btn_clear_anno.clicked.connect(self.clear_annotations)
            self.btn_clear_anno.setStyleSheet("""
                QPushButton { background: #26262D; border: none; font-size: 14px; }
                QPushButton:hover { background: #2C2C34; }
            """)

            anno_layout.addWidget(self.btn_pen)
            anno_layout.addWidget(self.btn_clear_anno)

            tools_row.addLayout(anno_layout)

            tools_row.addLayout(right_group)

            controls_layout.addLayout(tools_row)
            layout.addWidget(controls_container)

            # Set Default Enables
            self.btn_first.setEnabled(False)
            self.btn_prev.setEnabled(False)
            self.btn_play.setEnabled(False)
            self.btn_next.setEnabled(False)
            self.btn_last.setEnabled(False)

            # Smart Features State
            self.precache_worker = None
            self.is_hud_enabled = False
