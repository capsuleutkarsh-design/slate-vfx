"""
The preview player used by the Stock Viewer, Quick Look and the review dialog.

It routes a file to the right engine (a still, a movie, an image sequence),
and around the picture it has:

  * a scrub bar and a frame/time readout wide enough to read (MED-111), with
    the bar ending on the last frame, not one past it (MED-123);
  * drawn transport icons with their keys in the tooltips (MED-114, MED-117),
    driven by what the engine is doing rather than by the button's own text
    (MED-109);
  * the clip's sound, in step with the picture, with mute and volume - or
    "No sound" when it has none (MED-118);
  * for EXRs, the colour view and the input colourspace; the combos say what
    they are and are only there when they do something (MED-112, MED-124);
  * for stills, no transport that cannot do anything (MED-115);
  * Snapshot at the picture's own resolution, saved where you can find it and
    said so (MED-116); full screen as one expand/collapse icon (MED-113).

The picture area stays black in every theme: a neutral surround is what a
viewer needs to judge colour, as in RV or Nuke. The controls under it follow
the theme.
"""

import logging
import os
import subprocess
import sys
import threading
from datetime import datetime
from pathlib import Path

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QComboBox, QSlider, QSizePolicy, QFrame, QLabel, QMenu, QFileDialog,
    QStyle, QStyleOptionComboBox, QStylePainter,
)
from PySide6.QtCore import Qt, Signal, QTimer, QRect, QSize
from PySide6.QtGui import QPainter, QColor, QFont, QFontDatabase

from .media_engines.image_engine import ImageEngine
from .media_engines.stream_engine import StreamEngine
from .media_engines.sequence_engine import SequenceEngine
from .media_engines.audio_track import AudioTrack
from ..components.qt_safety import safe_single_shot
from ...utils.media_capabilities import is_video, is_image
from slate.core.infra.gate import Gate

# The viewer surround: black whatever the theme, with a light grey message.
SCREEN_BG = QColor(0, 0, 0)
SCREEN_TEXT = QColor(165, 165, 160)


class VideoWidget(QWidget):
    """
    Custom Widget for rendering video/images.
    Maintains fixed size policy (filling available space) but renders content
    aspect-ratio correct with letterboxing (black bars).
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.current_image = None
        self._text_msg = "Select an asset"
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)

    def set_image(self, image):
        self.current_image = image
        self.update()

    def set_text(self, text):
        self._text_msg = text
        self.current_image = None
        self.update()

    def text(self) -> str:
        return self._text_msg

    def message_rect(self) -> QRect:
        """Where a message is written: inside a margin, so long text wraps (MED-119)."""
        return self.rect().adjusted(16, 12, -16, -12)

    def paintEvent(self, event):
        painter = QPainter(self)
        rect = self.rect()
        # Every pixel is painted (WA_OpaquePaintEvent), so no trails are left.
        painter.fillRect(rect, SCREEN_BG)

        if self.current_image and not self.current_image.isNull():
            img_w = self.current_image.width() / max(1.0, self.current_image.devicePixelRatio())
            img_h = self.current_image.height() / max(1.0, self.current_image.devicePixelRatio())
            if img_w > 0 and img_h > 0:
                scale = min(rect.width() / img_w, rect.height() / img_h)
                target_w, target_h = int(img_w * scale), int(img_h * scale)
                target = QRect((rect.width() - target_w) // 2, (rect.height() - target_h) // 2,
                               target_w, target_h)
                painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
                painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
                painter.drawImage(target, self.current_image)
                painter.end()
                return

        if self._text_msg:
            painter.setPen(SCREEN_TEXT)
            font = painter.font()
            font.setPointSize(10)
            painter.setFont(font)
            painter.drawText(self.message_rect(),
                             Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap,
                             self._text_msg)
        painter.end()


def _mono_font() -> QFont:
    font = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
    font.setPointSize(9)
    return font


class ElidingComboBox(QComboBox):
    """
    A combo that never cuts its text mid-letter: when narrower than its text
    it shows "..." and keeps the full name in the tooltip.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.currentTextChanged.connect(self._update_tip)

    def _update_tip(self, text):
        base = self.property("base_tooltip") or ""
        self.setToolTip(f"{text}\n{base}".strip() if text else base)

    def minimumSizeHint(self):
        hint = super().minimumSizeHint()
        return QSize(min(hint.width(), 90), hint.height())

    def displayed_text(self) -> str:
        """What is painted: the current text, elided to the room there is."""
        option = QStyleOptionComboBox()
        self.initStyleOption(option)
        field = self.style().subControlRect(QStyle.ComplexControl.CC_ComboBox, option,
                                            QStyle.SubControl.SC_ComboBoxEditField, self)
        return self.fontMetrics().elidedText(self.currentText(), Qt.TextElideMode.ElideRight,
                                             max(10, field.width() - 2))

    def paintEvent(self, event):
        painter = QStylePainter(self)
        option = QStyleOptionComboBox()
        self.initStyleOption(option)
        painter.drawComplexControl(QStyle.ComplexControl.CC_ComboBox, option)
        option.currentText = self.displayed_text()
        painter.drawControl(QStyle.ControlElement.CE_ComboBoxLabel, option)


class AdvancedPlayer(QWidget):
    """
    Smart Host for Media Engines.
    Routes playback to ImageEngine, StreamEngine, or SequenceEngine.
    """
    frame_changed = Signal(int)       # Current frame
    duration_changed = Signal(int)    # Total frames
    next_requested = Signal()         # Playlist navigation
    prev_requested = Signal()
    playing_changed = Signal(bool)
    media_finished = Signal()         # a clip played to its end (loop off)
    snapshot_saved = Signal(str)
    _snapshot_done = Signal(str, str)  # path, error (from the snapshot thread)

    # Slow and fast enough to check an element or a sound (MED2-065).
    SPEEDS = ("0.25x", "0.5x", "1x", "2x", "4x")

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._is_closing = False

        self.active_engine = None
        self.engines = {
            'image': ImageEngine(self),
            'stream': StreamEngine(self),
            'sequence': SequenceEngine(self),
        }
        self.audio = AudioTrack(self)
        self.audio.availability_changed.connect(self._on_audio_available)

        self.is_slider_dragging = False
        self.total_frames = 1
        self.current_fps = 24.0
        # The rate an image sequence plays at: its project's (lineup.project_fps).
        self.sequence_fps = 24.0
        self.frame_offset = 0          # first frame number of a sequence
        self.show_timecode = False
        self.current_path = None
        self.media_kind = ""           # image / stream / sequence
        self._pending_autoplay = False
        self._sync_counter = 0

        self._scrub_timer = QTimer(self)
        self._scrub_timer.setSingleShot(True)
        self._scrub_timer.setInterval(150)
        self._scrub_timer.timeout.connect(self._do_scrub_seek)
        self._scrub_target = 0

        self._is_fullscreen = False
        self._cached_parent = None
        self._cached_layout = None
        self._cached_layout_index = -1
        self._cached_geometry = None
        self._placeholder = None

        # Load debounce: arrowing through a long list does not start a
        # decoder for every item on the way past.
        self._load_timer = QTimer(self)
        self._load_timer.setSingleShot(True)
        self._load_timer.setInterval(200)
        self._load_timer.timeout.connect(self._perform_load)
        self._pending_load_path = None
        self._pending_audio_path = None
        self._pending_first_frame = None
        self._error_details = ""

        self._snapshot_done.connect(self._on_snapshot_done)
        self.setup_ui()

    # ------------------------------------------------------------- layout
    def set_context(self, noun: str, play_keys: str = ""):
        """
        What the host calls the things Previous / Next move between, and the
        keys it offers for play: 'Next asset (Page Down)' in Quick Look, 'Next
        shot' in the Timeline - not the gallery's words everywhere (MED2-056).
        """
        self.btn_prev.setToolTip(f"Previous {noun}".strip())
        self.btn_next.setToolTip(f"Next {noun}".strip())
        keys = ", ".join(k for k in (play_keys, "K pauses, L plays") if k)
        self.btn_play.setToolTip(f"Play / pause ({keys})")

    def set_controls_visible(self, visible: bool):
        """Show/Hide internal controls (for embedding)."""
        if hasattr(self, 'controls_widget'):
            self.controls_widget.setVisible(visible)

    def _labelled_row(self, label, combo, tooltip):
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        caption = QLabel(label)
        caption.setObjectName("PlayerNoSound")      # the dim caption style
        caption.setMinimumWidth(36)
        layout.addWidget(caption)
        layout.addWidget(combo, 1)
        combo.setProperty("base_tooltip", tooltip)
        combo.setToolTip(tooltip)
        row.hide()
        return row

    def _icon_button(self, name, tooltip, checkable=False, width=30):
        from ..core.icons import icon as draw_icon
        button = QPushButton()
        button.setObjectName("PlayerButton")
        button.setIcon(draw_icon(name, Gate.TEXT, 16))
        button.setToolTip(tooltip)
        button.setCheckable(checkable)
        button.setFixedSize(width, 28)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        return button

    def setup_ui(self):
        from ..core.icons import icon as draw_icon
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.screen = VideoWidget()
        layout.addWidget(self.screen, 1)

        self.controls_widget = QWidget()
        self.controls_widget.setObjectName("PlayerControls")
        self.controls_widget.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.controls_widget.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self.controls_widget.setStyleSheet(Gate.sheet("""
            QWidget#PlayerControls { background: @PANEL; border-top: 1px solid @LINE_SOFT; }
            QPushButton#PlayerButton { background: transparent; border: 1px solid transparent;
                border-radius: 5px; padding: 0px; }
            QPushButton#PlayerButton:hover { background: @HOVER; border-color: @LINE; }
            QPushButton#PlayerButton:checked { background: @ACCENT_SURFACE; border-color: @ACCENT; }
            QPushButton#PlayerButton:disabled { background: transparent; }
            QPushButton#PlayerPlay { background: @ACCENT_SURFACE; border: 1px solid @ACCENT;
                border-radius: 5px; }
            QPushButton#PlayerPlay:hover { background: @ACCENT; }
            QPushButton#PlayerTime { background: transparent; border: none; color: @TEXT_2;
                text-align: right; padding: 0 2px; }
            QLabel#PlayerNoSound { color: @TEXT_DIM; }
            QSlider#PlayerScrub::groove:horizontal { height: 4px; background: @RAISED_HI;
                border-radius: 2px; margin: 5px 0; }
            QSlider#PlayerScrub::sub-page:horizontal { background: @ACCENT; border-radius: 2px; }
            QSlider#PlayerScrub::handle:horizontal { background: @TEXT; width: 12px; height: 12px;
                margin: -4px 0; border-radius: 6px; }
        """))

        controls_layout = QVBoxLayout(self.controls_widget)
        controls_layout.setContentsMargins(8, 6, 8, 6)
        controls_layout.setSpacing(4)

        # Row 1: scrub bar and readout
        row_scrub = QHBoxLayout()
        row_scrub.setSpacing(8)
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setObjectName("PlayerScrub")
        self.slider.setRange(0, 0)
        self.slider.setFixedHeight(16)
        self.slider.setCursor(Qt.CursorShape.PointingHandCursor)
        self.slider.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.slider.sliderPressed.connect(self.on_slider_pressed)
        self.slider.sliderReleased.connect(self.on_slider_released)
        self.slider.sliderMoved.connect(self.on_slider_move)

        self.lbl_time = QPushButton("")
        self.lbl_time.setObjectName("PlayerTime")
        self.lbl_time.setFlat(True)
        self.lbl_time.setFont(_mono_font())
        self.lbl_time.setCursor(Qt.CursorShape.PointingHandCursor)
        self.lbl_time.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.lbl_time.setToolTip("Frame number - click to show time instead")
        self.lbl_time.setMinimumWidth(96)
        self.lbl_time.clicked.connect(self.toggle_time_display)
        row_scrub.addWidget(self.slider, 1)
        row_scrub.addWidget(self.lbl_time)
        controls_layout.addLayout(row_scrub)

        # Row 2: transport, sound
        row_transport = QHBoxLayout()
        row_transport.setSpacing(4)
        self.btn_prev = self._icon_button("skip-previous", "Previous")
        self.btn_prev.clicked.connect(self.prev_requested.emit)
        # , and . step from wherever the keys come (the gallery passes them
        # on, where ← → move between cards) - MED2-007.
        self.btn_step_back = self._icon_button("step-back", "Back one frame (, or J)")
        self.btn_step_back.clicked.connect(lambda: self.step_active(-1))
        self.btn_play = self._icon_button("play", "Play / pause (K pauses, L plays)", width=40)
        self.btn_play.setObjectName("PlayerPlay")
        self.btn_play.clicked.connect(self.toggle_play)
        self.btn_step_forward = self._icon_button("step-forward", "Forward one frame (.)")
        self.btn_step_forward.clicked.connect(lambda: self.step_active(1))
        self.btn_next = self._icon_button("skip-next", "Next")
        self.btn_next.clicked.connect(self.next_requested.emit)

        row_transport.addStretch(1)
        for b in (self.btn_prev, self.btn_step_back, self.btn_play, self.btn_step_forward,
                  self.btn_next):
            row_transport.addWidget(b)
        row_transport.addStretch(1)

        self.btn_mute = self._icon_button("volume", "Mute (M)", checkable=True)
        self.btn_mute.toggled.connect(self.set_muted)
        self.volume_slider = QSlider(Qt.Orientation.Horizontal)
        self.volume_slider.setRange(0, 100)
        self.volume_slider.setValue(int(self.audio.volume() * 100))
        self.volume_slider.setFixedWidth(70)
        self.volume_slider.setToolTip("Volume")
        self.volume_slider.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.volume_slider.valueChanged.connect(lambda v: self.audio.set_volume(v / 100.0))
        self.lbl_no_audio = QLabel("No sound")
        self.lbl_no_audio.setObjectName("PlayerNoSound")
        self.lbl_no_audio.setToolTip("This clip has no sound track")
        row_transport.addWidget(self.btn_mute)
        row_transport.addWidget(self.volume_slider)
        row_transport.addWidget(self.lbl_no_audio)
        controls_layout.addLayout(row_transport)

        # Row 3 (EXR only): input colourspace and view, each on its own line
        # with the full width. In one row with speed and the tool buttons the
        # layout squeezed them to ~130 px and cut "Linear Rec.7(" (MED-112);
        # if a panel is narrower still they elide with "..." and the full
        # name is the tooltip.
        self.combo_input = ElidingComboBox()
        self.combo_input.currentIndexChanged.connect(self.change_input_space)
        self.combo_view = ElidingComboBox()
        self.combo_view.addItem("Standard")
        self.combo_view.currentIndexChanged.connect(self.change_view_transform)
        self.row_input = self._labelled_row("Input", self.combo_input,
                                            "Input colourspace of this EXR (read from the file "
                                            "when it says)")
        self.row_view = self._labelled_row("View", self.combo_view,
                                           "Colour view for EXRs. Other files are shown as they are.")
        controls_layout.addWidget(self.row_input)
        controls_layout.addWidget(self.row_view)

        # Row 4: speed, loop, snapshot, full screen
        row_tools = QHBoxLayout()
        row_tools.setSpacing(6)
        self.combo_speed = QComboBox()
        self.combo_speed.addItems(self.SPEEDS)
        self.combo_speed.setCurrentText("1x")
        self.combo_speed.setToolTip("Playback speed")
        self.combo_speed.setMinimumWidth(70)
        self.combo_speed.currentIndexChanged.connect(self.change_speed)
        for combo in (self.combo_input, self.combo_view, self.combo_speed):
            combo.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        row_tools.addWidget(self.combo_speed)
        row_tools.addStretch(1)

        self.btn_loop = self._icon_button("repeat", "Loop", checkable=True)
        self.btn_loop.setChecked(True)
        self.btn_loop.toggled.connect(self.toggle_loop)
        self.btn_snap = self._icon_button("image", "Save this frame as a picture, at full "
                                                   "resolution (right-click: Save as…)")
        self.btn_snap.clicked.connect(lambda: self.take_snapshot())
        self.btn_snap.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.btn_snap.customContextMenuRequested.connect(self._snapshot_menu)
        self.btn_fullscreen = self._icon_button("expand", "Full screen (F)")
        self.btn_fullscreen.clicked.connect(self.toggle_fullscreen)
        # Why a file would not play, in ffmpeg's words - for whoever asks
        # (MED2-012). Only there after an error.
        self.btn_details = QPushButton("Details")
        self.btn_details.setObjectName("PlayerButton")
        self.btn_details.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_details.setToolTip("What the decoder said")
        self.btn_details.clicked.connect(self._show_error_details)
        self.btn_details.hide()
        row_tools.addWidget(self.btn_details)
        row_tools.addWidget(self.btn_loop)
        row_tools.addWidget(self.btn_snap)
        row_tools.addWidget(self.btn_fullscreen)
        controls_layout.addLayout(row_tools)

        layout.addWidget(self.controls_widget, 0)
        self._set_play_icon(False)
        self._on_audio_available(False)
        self._configure_for(None)
        self.update_time_label(0)

    # ------------------------------------------------------------- state
    def is_playing(self) -> bool:
        return bool(self.active_engine and self.active_engine.is_playing())

    @property
    def paused(self) -> bool:
        return not self.is_playing()

    def _set_play_icon(self, playing: bool):
        from ..core.icons import icon as draw_icon
        self.btn_play.setIcon(draw_icon("pause" if playing else "play", Gate.TEXT, 16))
        self.btn_play.setProperty("playing", bool(playing))

    def _on_engine_state(self, playing: bool):
        sender = self.sender()
        if sender is not None and sender is not self.active_engine:
            return
        self._set_play_icon(playing)
        if self.media_kind == "stream":
            if playing:
                self.audio.play(self.active_engine.position_seconds())
            else:
                self.audio.pause()
        self.playing_changed.emit(bool(playing))

    def _on_audio_available(self, has_audio: bool):
        # Sound, or "No sound", only for a real movie: a picture-only source
        # (a sequence, a plate, their proxies) never has any (MED2-057).
        movie = self.media_kind == "stream" and getattr(self, "_sound_expected", False)
        self.btn_mute.setVisible(movie and has_audio)
        self.volume_slider.setVisible(movie and has_audio)
        self.lbl_no_audio.setVisible(movie and not has_audio)

    def has_audio(self) -> bool:
        return self.audio.has_audio

    def set_muted(self, muted: bool):
        from ..core.icons import icon as draw_icon
        self.audio.set_muted(muted)
        if self.btn_mute.isChecked() != bool(muted):
            self.btn_mute.setChecked(bool(muted))
        self.btn_mute.setIcon(draw_icon("volume-off" if muted else "volume", Gate.TEXT, 16))
        self.btn_mute.setToolTip("Unmute (M)" if muted else "Mute (M)")

    def _configure_for(self, kind):
        """Only the controls that do something for this kind of media (MED-115, MED-112)."""
        self.media_kind = kind or ""
        moving = kind in ("stream", "sequence")
        for widget in (self.slider, self.lbl_time, self.btn_step_back, self.btn_play,
                       self.btn_step_forward, self.combo_speed, self.btn_loop):
            widget.setVisible(moving)
        engine = self.engines['image']
        exr = kind == "image" and getattr(engine, "is_exr", False)
        has_views = bool(getattr(engine, "views", None))
        self.combo_view.setEnabled(exr and has_views)
        self.row_view.setVisible(exr and has_views)
        self.row_input.setVisible(exr and bool(getattr(engine, "input_spaces", None)))
        self.btn_snap.setEnabled(kind is not None)
        self._on_audio_available(self.audio.has_audio)

    # ------------------------------------------------------------- loading
    def load(self, path, audio_source=None, first_frame=None):
        """
        Load a file (debounced).

        When path is a proxy: audio_source is the original (where the sound
        is, and what a snapshot is named after), and first_frame the original
        sequence's first frame number, so the readout counts 1001 / 1024 and
        not 1 / 24 (MED2-015, MED2-047).
        """
        str_path = str(path)
        if getattr(self, 'current_path', None) == str_path:
            if self._pending_autoplay and not self.is_playing() and self.active_engine:
                self._pending_autoplay = False
                self.active_engine.play()
            return
        self.stop_media()
        self.btn_details.hide()
        self.current_path = str_path
        self._pending_load_path = str_path
        self._pending_audio_path = str(audio_source) if audio_source else str_path
        self._pending_first_frame = first_frame
        self.screen.set_text("Loading…")
        self._load_timer.start(200)

    def _perform_load(self):
        """Called by timer to actually load the media."""
        path = getattr(self, '_pending_load_path', None)
        if not path or path != self.current_path:
            return
        logging.info(f"AdvancedPlayer: Loading {path}")
        path_obj = Path(path)

        engine_key = 'stream'
        seq = None
        if not is_video(path_obj.suffix.lower()):
            from ...utils.sequence_utils import sequence_for
            try:
                seq = sequence_for(path_obj)
                # The library's rule: numbered variants are stills.
                from ...core.domain.sequence_rules import is_real_sequence
                if seq is not None and not is_real_sequence(seq):
                    seq = None
            except Exception as e:
                logging.exception(f"Sequence detection error: {e}")
            if seq is not None:
                engine_key = 'sequence'
                # The whole range: a missing frame is held, not skipped.
                self.engines['sequence'].set_sequence_details(
                    seq.pattern, seq.start, seq.end - seq.start + 1, seq, fps=self.sequence_fps)
            elif is_image(path_obj.suffix.lower()):
                engine_key = 'image'

        self._activate_engine(engine_key)
        self.frame_offset = seq.start if seq is not None else int(self._pending_first_frame or 0)
        sound = self._pending_audio_path or path
        self._sound_expected = engine_key == 'stream' and is_video(Path(sound).suffix.lower())
        size = self.screen.size()
        self.active_engine.set_pixel_ratio(self.devicePixelRatio())
        self.active_engine.set_target_size(size)

        try:
            self.active_engine.load(str(path))
            self._fill_colour_combos()
            self._configure_for(engine_key)
            if self._sound_expected:
                self.audio.load(sound)
            else:
                self.audio.clear()
            # Selecting shows the first frame; it plays only when asked.
            if self._pending_autoplay and engine_key != 'image':
                self.active_engine.play()
            self._pending_autoplay = False
        except Exception as e:
            logging.exception(f"Engine Load Error: {e}")
            self.screen.set_text("This file could not be opened.")
            try:
                self.stop_media()
            except RuntimeError as stop_err:
                logging.debug(f"stop_media failed during engine-load error recovery: {stop_err}")

    def _fill_colour_combos(self):
        engine = self.active_engine
        self.combo_view.blockSignals(True)
        self.combo_view.clear()
        views = getattr(engine, 'views', None) if engine is self.engines['image'] else None
        if views:
            for v in views:
                self.combo_view.addItem(v)
            self.combo_view.setCurrentText(getattr(engine, 'view', None) or views[0])
        else:
            self.combo_view.addItem("Standard")
        self.combo_view.blockSignals(False)

        self.combo_input.blockSignals(True)
        self.combo_input.clear()
        spaces = list(getattr(engine, 'input_spaces', None) or []) if engine is self.engines['image'] else []
        current = getattr(engine, 'input_space', "") if spaces else ""
        if current and current not in spaces:
            spaces.insert(0, current)
        for space in spaces:
            self.combo_input.addItem(space)
        if current:
            self.combo_input.setCurrentText(current)
        self.combo_input.blockSignals(False)

    def change_view_transform(self, index):
        """Update OCIO View"""
        view = self.combo_view.currentText()
        if hasattr(self.active_engine, 'set_view_transform'):
            display = getattr(self.active_engine, 'display', 'sRGB')
            self.active_engine.set_view_transform(display, view)

    def change_input_space(self, index):
        space = self.combo_input.currentText()
        if space and hasattr(self.active_engine, 'set_input_space'):
            self.active_engine.set_input_space(space)

    def _activate_engine(self, key):
        """Disconnect old, Connect new."""
        if self.active_engine:
            for signal, slot in self._engine_links():
                try:
                    signal.disconnect(slot)
                except (TypeError, RuntimeError):
                    pass
        self.active_engine = self.engines[key]
        for signal, slot in self._engine_links():
            signal.connect(slot)
        self.active_engine.set_target_size(self.screen.size())
        self.active_engine.set_speed(self.get_current_speed())
        self.active_engine.set_loop(self.btn_loop.isChecked())
        try:
            self.active_engine.set_pixel_ratio(self.devicePixelRatio())
        except AttributeError:
            pass

    def _engine_links(self):
        e = self.active_engine
        return ((e.frame_ready, self.update_screen), (e.position_changed, self.update_slider_pos),
                (e.duration_changed, self.set_duration), (e.finished, self.on_engine_finished),
                (e.error_occurred, self.on_engine_error), (e.state_changed, self._on_engine_state),
                (e.looped, self._on_looped))

    def on_engine_finished(self):
        sender = self.sender()
        if sender is not None and sender is not self.active_engine:
            return
        if self._is_closing:
            return
        self._set_play_icon(False)
        self.audio.pause()
        self.media_finished.emit()

    def _on_looped(self):
        if self.media_kind == "stream":
            self.audio.seek(0.0)

    def on_engine_error(self, message):
        sender = self.sender()
        if sender is not None and sender is not self.active_engine:
            return
        if self._is_closing:
            return
        logging.error(f"Engine Error: {message}")
        from .media_engines.stream_engine import FFMPEG_MISSING
        if message == FFMPEG_MISSING:
            self.show_message(message)
            return
        # A sentence on the picture, the decoder's own words behind Details,
        # and no transport left that cannot do anything (MED2-012).
        self.show_message("This file could not be played - it may be damaged or in a format "
                          "this machine cannot read.", details=message)

    def show_message(self, text, details=""):
        """
        Stop and show only a sentence: no transport or colour controls left
        over from the clip before (NEW-media-3).
        """
        self.stop_media()
        self._configure_for(None)
        self.screen.set_text(text)
        self._error_details = details
        self.btn_details.setVisible(bool(details))

    def _show_error_details(self):
        from ..components.feedback import show_details
        show_details(self, "This file could not be played.", self._error_details)

    def stop_media(self):
        if self.active_engine:
            self.active_engine.stop()
        self.audio.clear()
        self._set_play_icon(False)
        self.slider.setValue(0)
        self.update_time_label(0)
        self.current_path = None

    # --------------------------------------------------------- controls
    def toggle_play(self):
        if self._load_timer.isActive():
            # Still about to load: play it when it is in.
            self._pending_autoplay = not self._pending_autoplay
            return
        if not self.active_engine or self.media_kind == "image":
            return
        if self.is_playing():
            self.active_engine.pause()
        else:
            self.active_engine.play()

    def play(self):
        if self._load_timer.isActive():
            self._pending_autoplay = True
            return
        if self.active_engine and self.media_kind != "image":
            self.active_engine.play()

    def pause(self):
        if self.active_engine:
            self.active_engine.pause()

    def step_active(self, frames):
        if self.active_engine and self.media_kind != "image":
            self.active_engine.step(frames)
            self.audio.pause()

    def seek(self, frame):
        if self.active_engine:
            self.active_engine.seek(frame)
            if self.media_kind == "stream" and self.current_fps > 0:
                self.audio.seek(frame / self.current_fps)

    def toggle_loop(self):
        loop = self.btn_loop.isChecked()
        if self.active_engine:
            self.active_engine.set_loop(loop)

    def change_speed(self):
        speed = self.get_current_speed()
        if self.active_engine:
            self.active_engine.set_speed(speed)
        self.audio.set_rate(speed)

    def get_current_speed(self):
        txt = self.combo_speed.currentText()
        try:
            return float(txt.replace('x', ''))
        except ValueError:
            return 1.0

    # ------------------------------------------------------------- updates
    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.active_engine:
            self.active_engine.set_pixel_ratio(self.devicePixelRatio())
            self.active_engine.set_target_size(self.screen.size())

    def update_screen(self, image):
        self.screen.set_image(image)

    def set_duration(self, frames):
        """The bar runs from the first frame to the last - not one past it (MED-123)."""
        self.total_frames = max(1, int(frames or 1))
        self.slider.setRange(0, self.total_frames - 1)
        if hasattr(self.active_engine, 'fps') and self.active_engine.fps:
            self.current_fps = self.active_engine.fps
        self.update_time_label(self.slider.value())
        self.duration_changed.emit(self.total_frames)

    def update_slider_pos(self, frame):
        if not self.is_slider_dragging:
            self.slider.setValue(frame)
            self.update_time_label(frame)
        self.frame_changed.emit(frame)
        if self.media_kind == "stream":
            self._sync_counter += 1
            if self._sync_counter % 12 == 0:
                self.audio.sync(self.active_engine.position_seconds(), self.is_playing())

    def on_slider_pressed(self):
        self.is_slider_dragging = True
        self._was_playing = self.is_playing()
        if self.active_engine:
            self.active_engine.pause()

    def on_slider_released(self):
        self.is_slider_dragging = False
        self.seek(self.slider.value())
        if getattr(self, "_was_playing", False) and self.active_engine:
            self.active_engine.play()

    def on_slider_move(self, val):
        self.update_time_label(val)
        if self.is_slider_dragging and self.active_engine:
            self._scrub_target = val
            self._scrub_timer.start()

    def _do_scrub_seek(self):
        if self.active_engine and self.is_slider_dragging:
            self.active_engine.seek(self._scrub_target)

    def time_text(self, frame) -> str:
        """'12 / 48' (or source frame numbers for a sequence), or a timecode."""
        frame = max(0, int(frame or 0))
        last = max(0, self.total_frames - 1)
        if self.show_timecode and self.current_fps > 0:
            # The same h:mm:ss:ff as the Stock Viewer's facts (MED2-031).
            from ..stock_model import timecode
            fps = self.current_fps
            return f"{timecode(frame / fps, fps)} / {timecode(last / fps, fps)}"
        if self.frame_offset:
            return f"{self.frame_offset + frame} / {self.frame_offset + last}"
        return f"{frame + 1} / {last + 1}"

    def update_time_label(self, frame):
        self.lbl_time.setText(self.time_text(frame))
        hint = self.lbl_time.fontMetrics().horizontalAdvance(self.lbl_time.text()) + 12
        if hint > self.lbl_time.minimumWidth():
            self.lbl_time.setMinimumWidth(hint)

    def toggle_time_display(self):
        self.show_timecode = not self.show_timecode
        self.lbl_time.setToolTip("Time - click to show frame numbers" if self.show_timecode
                                 else "Frame number - click to show time instead")
        self.update_time_label(self.slider.value())

    # ------------------------------------------------------------- snapshot
    @staticmethod
    def snapshot_folder() -> Path:
        return Path.home() / "Pictures" / "Slate_Snaps"

    def _snapshot_menu(self, pos):
        menu = QMenu(self)
        menu.addAction("Save snapshot", lambda: self.take_snapshot())
        menu.addAction("Save snapshot as…", self._snapshot_as)
        menu.exec(self.btn_snap.mapToGlobal(pos))

    def _snapshot_as(self):
        default = str(self.snapshot_folder() / self._snapshot_name())
        path, _ = QFileDialog.getSaveFileName(self, "Save snapshot", default,
                                              "PNG picture (*.png);;JPEG picture (*.jpg)")
        if path:
            self.take_snapshot(path)

    def _snapshot_source(self):
        """
        The original file when the player shows a proxy of it.

        The Stock Viewer plays the cache proxy (1920 px at most, named by a
        hash) and passes the original as the sound source; a snapshot is taken
        from, and named after, the original (NEW-media-4).
        """
        original = self._pending_audio_path
        # A movie or a still; a sequence's proxy is snapshotted from the proxy
        # (its "original" is the first frame file, which cannot be seeked).
        usable = self.media_kind == "image" or is_video(Path(str(original)).suffix.lower())
        if original and original != self.current_path and usable:
            from slate.core.domain.proxy_manager import ProxyManager
            if ProxyManager.exists(original):
                return original
        return None

    def _snapshot_name(self) -> str:
        stem = Path(self._pending_audio_path or self.current_path or "frame").stem or "frame"
        return f"{stem}_f{self.slider.value() + max(self.frame_offset, 1)}_{datetime.now():%Y%m%d_%H%M%S}.png"

    def take_snapshot(self, path: str = None):
        """
        Save the frame at the picture's own resolution and say where.

        It used to save what was on screen - a scaled-down frame - silently,
        to a folder nobody was told about (MED-116).
        """
        if not self.active_engine or not self.current_path:
            return None
        target = Path(path) if path else self.snapshot_folder() / self._snapshot_name()
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            self._snapshot_done.emit("", str(exc))
            return None
        engine = self.active_engine
        original = self._snapshot_source()
        if self.media_kind == "image" and not original:
            image = self.active_engine.full_frame()
            ok = bool(image is not None and not image.isNull() and image.save(str(target)))
            self._snapshot_done.emit(str(target) if ok else "", "" if ok else "The picture could not be written.")
            return str(target) if ok else None

        frame = int(getattr(engine, "current_frame", 0) or 0)
        fps = float(getattr(engine, "fps", 24.0) or 24.0)
        ffmpeg = getattr(engine, "ff_path", None) or getattr(self.engines['stream'], "ff_path", None)
        if not ffmpeg:
            self._snapshot_done.emit("", "ffmpeg was not found.")
            return None
        if original:
            seek = [] if self.media_kind == "image" else ["-ss", f"{frame / fps:.3f}"]
            cmd = [ffmpeg, "-y", "-loglevel", "error"] + seek + [
                   "-i", original, "-frames:v", "1", str(target)]
        elif self.media_kind == "sequence":
            cmd = [ffmpeg, "-y", "-loglevel", "error", "-start_number",
                   str(engine.start_frame_idx + frame), "-i", engine.source, "-frames:v", "1",
                   str(target)]
        else:
            cmd = [ffmpeg, "-y", "-loglevel", "error", "-ss", f"{frame / fps:.3f}",
                   "-i", engine.source, "-frames:v", "1", str(target)]

        def run():
            try:
                kwargs = {}
                if sys.platform == "win32":
                    kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=60, **kwargs)
                ok = result.returncode == 0 and target.exists()
                self._snapshot_done.emit(str(target) if ok else "",
                                         "" if ok else (result.stderr.strip() or "ffmpeg failed"))
            except Exception as exc:
                self._snapshot_done.emit("", str(exc))

        threading.Thread(target=run, daemon=True, name="slate-snapshot").start()
        return str(target)

    def _on_snapshot_done(self, path, error):
        from ..components.feedback import toast
        from PySide6.QtGui import QDesktopServices
        from PySide6.QtCore import QUrl
        if path:
            folder = str(Path(path).parent)
            self.snapshot_saved.emit(path)
            toast(self, f"Snapshot saved: {Path(path).name}", "success",
                  action=("Open folder", lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(folder))))
        else:
            toast(self, "The snapshot could not be saved.", "error", details=error)

    # ------------------------------------------------------------- full screen
    def toggle_fullscreen(self):
        """Toggle fullscreen mode seamlessly without rebuilding the player."""
        from ..core.icons import icon as draw_icon
        if not self._is_fullscreen:
            global_pos = self.mapToGlobal(self.rect().topLeft())
            self._cached_parent = self.parentWidget()
            self._cached_layout = self._cached_parent.layout() if self._cached_parent else None
            self._cached_layout_index = -1
            self._cached_geometry = self.geometry()
            if self._cached_layout:
                parent_layout = self._cached_layout
                for i in range(parent_layout.count()):
                    item = parent_layout.itemAt(i)
                    if item and item.widget() == self:
                        self._cached_layout_index = i
                        self._placeholder = QWidget(self._cached_parent)
                        self._placeholder.setSizePolicy(self.sizePolicy())
                        self._placeholder.setMinimumSize(self.minimumSize())
                        parent_layout.replaceWidget(self, self._placeholder)
                        break
            self.hide()
            self.setParent(None)
            self.setWindowFlags(Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint
                                | Qt.WindowType.WindowStaysOnTopHint)
            self.move(global_pos)
            self.showFullScreen()
            self._is_fullscreen = True
            self.btn_fullscreen.setIcon(draw_icon("collapse", Gate.TEXT, 16))
            self.btn_fullscreen.setToolTip("Leave full screen (Esc or F)")
            self.setFocus()
        else:
            self.hide()
            # Reparent BEFORE changing window flags: setWindowFlags on a
            # parentless widget recreates the native window.
            if self._cached_parent:
                self.setParent(self._cached_parent)
            self.setWindowFlags(Qt.WindowType.Widget)
            if self._placeholder and self._cached_layout:
                self._cached_layout.replaceWidget(self._placeholder, self)
                self._placeholder.deleteLater()
                self._placeholder = None
            self.show()
            self.raise_()
            self.activateWindow()
            if self.controls_widget:
                self.controls_widget.setEnabled(True)
                self.controls_widget.setVisible(True)
            self._is_fullscreen = False
            self.btn_fullscreen.setIcon(draw_icon("expand", Gate.TEXT, 16))
            self.btn_fullscreen.setToolTip("Full screen (F)")
            self._cached_parent = None
            self._cached_layout = None
            self._cached_layout_index = -1
            self.setFocus()
            safe_single_shot(0, self, self._refresh_after_fullscreen_restore)

    def _refresh_after_fullscreen_restore(self):
        if not self.active_engine:
            return
        try:
            self.active_engine.set_pixel_ratio(self.devicePixelRatio())
            self.active_engine.set_target_size(self.screen.size())
        except Exception as exc:
            logging.debug("AdvancedPlayer: fullscreen restore refresh skipped: %s", exc)

    # ------------------------------------------------------------- keys
    def handle_key(self, event) -> bool:
        """
        The player's keys, whoever has the focus (MED-117): the gallery and
        Quick Look pass them on, so they work without clicking into the player.
        """
        if not self.active_engine:
            return False
        if event.isAutoRepeat() and event.key() not in (Qt.Key.Key_Left, Qt.Key.Key_Right,
                                                         Qt.Key.Key_Comma, Qt.Key.Key_Period):
            return True
        key = event.key()
        if key == Qt.Key.Key_Space:
            self.toggle_play()
        elif key in (Qt.Key.Key_Right, Qt.Key.Key_Period):
            self.step_active(1)
        elif key in (Qt.Key.Key_Left, Qt.Key.Key_Comma, Qt.Key.Key_J):
            # ffmpeg cannot decode backwards, so J steps back a frame.
            self.step_active(-1)
        elif key == Qt.Key.Key_L:
            self.play()
        elif key == Qt.Key.Key_K:
            self.pause()
        elif key == Qt.Key.Key_M:
            if self.audio.has_audio:
                self.set_muted(not self.audio.is_muted())
        elif key == Qt.Key.Key_Home:
            self.seek(0)
        elif key == Qt.Key.Key_End:
            self.seek(self.total_frames - 1)
        elif key == Qt.Key.Key_Escape and self._is_fullscreen:
            safe_single_shot(0, self, self.toggle_fullscreen)
        elif key == Qt.Key.Key_F:
            safe_single_shot(0, self, self.toggle_fullscreen)
        else:
            return False
        return True

    def keyPressEvent(self, event):
        if self.hasFocus() and self.handle_key(event):
            event.accept()
            return
        super().keyPressEvent(event)

    def closeEvent(self, event):
        self._is_closing = True
        self.stop_media()
        super().closeEvent(event)
