"""
The Stock Viewer's right panel: the preview and the facts about one asset.

  * Facts read from the file: type, resolution, length (a timecode, or a
    frame range for a sequence), frame rate, codec, size, category, when it
    was added and by whom, and where it lives - with Copy path and Show in
    Explorer (MED-058). A still says "Still" and has no frame rate or length
    (MED-020). Missing values are one em dash; "Not analysed yet" and
    "Analysing…" are states with the same font, only the colour changes
    (MED-071, MED-072).
  * A long name wraps at _ . - instead of being cut off (MED-057).
  * Star (yours), Studio pick and Edit tags (for people who manage the
    library) (MED-009, MED-031).
  * A file that is not there says so in words, with its path; camera raw says
    there is no preview and offers to open it in its own program - nothing is
    launched on a double-click any more (MED-059, MED-060).
  * Selecting shows the first frame; playing is Enter, Space in Quick Look, a
    double-click, or "Play when selected" for those who want it (MED-073).
"""

import json
import logging
import os
from pathlib import Path

from PySide6.QtCore import Qt, Signal, QSettings, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QGridLayout, QSizePolicy, QScrollArea,
    QCheckBox, QApplication, QFrame,
)

from ....widgets.advanced_player import AdvancedPlayer
from ....core.controls import make_button
from ....core.icons import icon as draw_icon
from ....stock_model import (
    DASH, KIND_NAMES, asset_kind, asset_path, display_name, length_text, resolution_text,
    size_text, added_text, can_preview,
)
from slate.core.infra.gate import Gate

ZERO_WIDTH_SPACE = "​"


def wrappable(name: str) -> str:
    """The name with invisible break points after _ . - so it wraps instead of clipping."""
    out = []
    for ch in str(name or ""):
        out.append(ch)
        if ch in "_.-/\\":
            out.append(ZERO_WIDTH_SPACE)
    return "".join(out)


def _meta(asset):
    meta = asset.get("metadata") if asset else {}
    if isinstance(meta, str):
        try:
            meta = json.loads(meta) if meta else {}
        except ValueError:
            meta = {}
    return meta if isinstance(meta, dict) else {}


def fps_text(asset) -> str:
    meta = _meta(asset)
    if meta.get("is_still") or asset.get("is_sequence") or asset_kind(asset) != "MOV":
        return DASH
    try:
        fps = float(meta.get("fps") or 0)
    except (TypeError, ValueError):
        fps = 0.0
    return f"{fps:.3f}".rstrip("0").rstrip(".") if fps > 0 else DASH


def codec_text(asset) -> str:
    codec = str(_meta(asset).get("codec") or "").strip()
    return codec.upper() if codec and codec.lower() != "unknown" else DASH


def analysed(asset) -> bool:
    meta = _meta(asset)
    return bool(meta.get("width") or meta.get("duration_sec") or meta.get("raw")
                or meta.get("frame_count"))


class StockInspectorPanel(QWidget):
    """
    Inspector Panel for Stock Browser.
    """
    next_requested = Signal()
    prev_requested = Signal()
    analysis_requested = Signal(str, dict)   # path, asset_dict
    favorite_toggled = Signal(dict, bool)
    pick_toggled = Signal(dict, bool)
    tags_edit_requested = Signal(dict)

    FIELDS = (("type", "Type"), ("resolution", "Resolution"), ("length", "Length"),
              ("fps", "Frame rate"), ("codec", "Codec"), ("size", "Size"),
              ("category", "Category"), ("added", "Added"), ("added_by", "Added by"))

    def __init__(self, parent=None, can_manage=False):
        super().__init__(parent)
        self.can_manage = bool(can_manage)
        self.current_asset = None
        self._settings = QSettings("Slate", "StockViewer")
        self.setup_ui()

    # ------------------------------------------------------------- layout
    def setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.setMinimumWidth(280)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)

        self.player = AdvancedPlayer()
        self.player.setMinimumHeight(240)
        self.player.setMaximumHeight(600)
        self.player.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.player.next_requested.connect(self.next_requested.emit)
        self.player.prev_requested.connect(self.prev_requested.emit)
        layout.addWidget(self.player, 3)

        # What to do when there is no picture: a missing file, camera raw.
        self.notice_row = QWidget()
        notice = QHBoxLayout(self.notice_row)
        notice.setContentsMargins(12, 6, 12, 0)
        self.btn_open_external = make_button("Open with its own program", "secondary", icon="external",
                                             tooltip="Open the file in the program Windows uses for it")
        self.btn_open_external.clicked.connect(self._open_externally)
        self.btn_copy_missing = make_button("Copy path", "secondary", icon="copy")
        self.btn_copy_missing.clicked.connect(self.copy_path)
        notice.addWidget(self.btn_open_external)
        notice.addWidget(self.btn_copy_missing)
        notice.addStretch()
        self.notice_row.hide()
        layout.addWidget(self.notice_row)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.meta_group = QWidget()
        self.meta_group.setObjectName("StockInspectorFacts")
        meta_layout = QVBoxLayout(self.meta_group)
        meta_layout.setContentsMargins(14, 12, 14, 12)
        meta_layout.setSpacing(10)
        scroll.setWidget(self.meta_group)

        self.lbl_name = QLabel(DASH)
        self.lbl_name.setObjectName("StockAssetName")
        self.lbl_name.setWordWrap(True)
        self.lbl_name.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        meta_layout.addWidget(self.lbl_name)

        action_row = QHBoxLayout()
        action_row.setSpacing(6)
        self.btn_favorite = make_button("Favourite", "secondary", icon="star",
                                        tooltip="Keep it in your Favorites (Ctrl+D)")
        self.btn_favorite.setCheckable(True)
        self.btn_favorite.clicked.connect(self._favorite_clicked)
        self.btn_pick = make_button("Studio pick", "secondary", icon="sparkle",
                                    tooltip="Show it in Studio picks for everybody")
        self.btn_pick.setCheckable(True)
        self.btn_pick.clicked.connect(self._pick_clicked)
        self.btn_tags = make_button("Edit tags…", "secondary", icon="tag")
        self.btn_tags.clicked.connect(lambda: self.current_asset and
                                      self.tags_edit_requested.emit(self.current_asset))
        for b in (self.btn_favorite, self.btn_pick, self.btn_tags):
            action_row.addWidget(b)
        action_row.addStretch()
        meta_layout.addLayout(action_row)

        self.facts = QWidget()
        self.facts.setObjectName("StockFacts")
        grid = QGridLayout(self.facts)
        grid.setContentsMargins(12, 10, 12, 10)
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(6)
        self.values = {}
        for row, (key, label) in enumerate(self.FIELDS):
            caption = QLabel(label)
            caption.setProperty("role", "caption")
            value = QLabel(DASH)
            value.setProperty("state", "none")
            value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            grid.addWidget(caption, row, 0, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop)
            grid.addWidget(value, row, 1)
            self.values[key] = value
        grid.setColumnStretch(1, 1)
        meta_layout.addWidget(self.facts)

        # Back-compat names for callers and tests.
        self.lbl_res = self.values["resolution"]
        self.lbl_fps = self.values["fps"]
        self.lbl_dur = self.values["length"]

        caption = QLabel("Location")
        caption.setProperty("role", "caption")
        meta_layout.addWidget(caption)
        self.lbl_path = QLabel(DASH)
        self.lbl_path.setWordWrap(True)
        self.lbl_path.setProperty("state", "none")
        self.lbl_path.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        meta_layout.addWidget(self.lbl_path)
        path_row = QHBoxLayout()
        self.btn_copy_path = make_button("Copy path", "ghost", icon="copy")
        self.btn_copy_path.clicked.connect(self.copy_path)
        self.btn_reveal = make_button("Show in Explorer", "ghost", icon="folder")
        self.btn_reveal.clicked.connect(self._reveal)
        path_row.addWidget(self.btn_copy_path)
        path_row.addWidget(self.btn_reveal)
        path_row.addStretch()
        meta_layout.addLayout(path_row)

        caption = QLabel("Tags")
        caption.setProperty("role", "caption")
        meta_layout.addWidget(caption)
        self.lbl_tags = QLabel(DASH)
        self.lbl_tags.setWordWrap(True)
        self.lbl_tags.setProperty("state", "none")
        self.lbl_tags.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        meta_layout.addWidget(self.lbl_tags)

        self.chk_autoplay = QCheckBox("Play when selected")
        self.chk_autoplay.setToolTip("Start playing a clip as soon as it is selected. "
                                     "Off: the first frame shows, Enter plays.")
        try:
            self.chk_autoplay.setChecked(self._settings.value("autoplay", False, type=bool))
        except Exception:
            self.chk_autoplay.setChecked(False)
        self.chk_autoplay.toggled.connect(lambda on: self._settings.setValue("autoplay", bool(on)))
        meta_layout.addWidget(self.chk_autoplay)
        meta_layout.addStretch()
        layout.addWidget(scroll, 2)

        # Captions and values keep one font; only the colour says the state.
        self.meta_group.setStyleSheet(Gate.sheet("""
            QWidget#StockInspectorFacts { background: transparent; }
            QLabel#StockAssetName { font-size: 15px; font-weight: 600; color: @TEXT; }
            QWidget#StockFacts { background: @PANEL; border: 1px solid @LINE_SOFT; border-radius: 6px; }
            QLabel[role="caption"] { color: @TEXT_DIM; }
            QLabel[state="value"] { color: @TEXT; }
            QLabel[state="none"] { color: @TEXT_DIM; }
            QLabel[state="analysing"] { color: @WARN; }
            QLabel[state="bad"] { color: @BAD; }
        """))
        self.set_permissions(self.can_manage)
        self.clear()

    def set_permissions(self, can_manage: bool):
        self.can_manage = bool(can_manage)
        self.btn_pick.setVisible(self.can_manage)
        self.btn_tags.setVisible(self.can_manage)

    # ------------------------------------------------------------- values
    @staticmethod
    def _set(label, text, state="value"):
        label.setText(str(text) if text not in (None, "") else DASH)
        if text in (None, "", DASH):
            state = "none" if state == "value" else state
        if label.property("state") != state:
            label.setProperty("state", state)
            label.style().unpolish(label)
            label.style().polish(label)

    def wants_autoplay(self) -> bool:
        return self.chk_autoplay.isChecked()

    def clear(self):
        """Nothing selected: stop the player and say so (MED-019)."""
        self.current_asset = None
        try:
            self.player.stop_media()
        except Exception:
            pass
        self.player.screen.set_text("Select an asset")
        self.lbl_name.setText("Nothing selected")
        self.lbl_name.setToolTip("")
        for label in self.values.values():
            self._set(label, DASH)
        self._set(self.lbl_path, DASH)
        self._set(self.lbl_tags, DASH)
        self.notice_row.hide()
        for button in (self.btn_favorite, self.btn_pick, self.btn_tags, self.btn_copy_path,
                       self.btn_reveal):
            button.setEnabled(False)
        self.btn_favorite.setChecked(False)
        self.btn_pick.setChecked(False)

    def update_asset(self, asset, autoplay=None):
        """Show an asset: facts first, then the preview."""
        self.current_asset = asset
        if not asset:
            self.clear()
            return
        try:
            self._show_facts(asset)
            self._show_preview(asset, self.wants_autoplay() if autoplay is None else autoplay)
        except Exception as e:
            logging.exception(f"Error updating inspector: {e}")
            self.player.screen.set_text("This asset could not be shown.")

    def refresh_facts(self, asset):
        """New facts for the asset already shown (after a background analysis)."""
        if self.current_asset is not None and asset_path(asset) == asset_path(self.current_asset):
            self.current_asset.update(asset)
            self._show_facts(self.current_asset)

    def _show_facts(self, asset):
        name = display_name(asset)
        self.lbl_name.setText(wrappable(name))
        self.lbl_name.setToolTip(name)
        kind = asset_kind(asset)
        status = asset.get('status', 'ready')

        self._set(self.values["type"], KIND_NAMES.get(kind, "File"))
        if status in ('pending', 'ingesting'):
            for key in ("resolution", "length", "fps", "codec"):
                self._set(self.values[key], "Analysing…", "analysing")
        elif not analysed(asset) and kind != "RAW":
            self._set(self.values["resolution"], "Not analysed yet", "none")
            for key in ("length", "fps", "codec"):
                self._set(self.values[key], DASH)
            path = asset_path(asset)
            if path and not asset.get('_missing') and os.path.exists(path):
                asset['status'] = 'ingesting'
                self._set(self.values["resolution"], "Analysing…", "analysing")
                self.analysis_requested.emit(path, asset)
        else:
            self._set(self.values["resolution"], resolution_text(asset))
            self._set(self.values["length"], length_text(asset))
            self._set(self.values["fps"], fps_text(asset))
            self._set(self.values["codec"], codec_text(asset))
        self._set(self.values["size"], size_text(asset.get('file_size')))
        self._set(self.values["category"], asset.get('category') or DASH)
        self._set(self.values["added"], added_text(asset))
        added_by = asset.get('added_by') or ""
        if added_by:
            try:
                from slate.core.domain.people import display_name as person
                added_by = person(added_by)
            except Exception:
                pass
        self._set(self.values["added_by"], added_by or DASH)

        path = asset_path(asset)
        self._set(self.lbl_path, wrappable(path) if path else DASH,
                  "bad" if asset.get('_missing') else "value")
        tags = asset.get('tags') or []
        if isinstance(tags, str):
            from slate.core.domain.stock_search import real_tags
            tags = real_tags(tags)
        visual = asset.get('visual_tags') or []
        text = " · ".join(list(tags) + [v for v in visual if v not in tags])
        self._set(self.lbl_tags, text or DASH)

        for button in (self.btn_favorite, self.btn_copy_path, self.btn_reveal):
            button.setEnabled(bool(path))
        self.btn_pick.setEnabled(str(asset.get('id', '')).isdigit())
        self.btn_tags.setEnabled(str(asset.get('id', '')).isdigit())
        self.btn_favorite.setEnabled(str(asset.get('id', '')).isdigit())
        self.set_favorite(bool(asset.get('is_favorite')))
        self.set_pick(bool(asset.get('is_pick')))

    def set_favorite(self, on: bool):
        self.btn_favorite.setChecked(bool(on))
        self.btn_favorite.setText("Favourite" if not on else "In favourites")
        self.btn_favorite.setIcon(draw_icon("star-filled" if on else "star",
                                            Gate.WARN if on else Gate.TEXT, 16))

    def set_pick(self, on: bool):
        self.btn_pick.setChecked(bool(on))
        self.btn_pick.setText("Studio pick" if not on else "Picked")

    def _show_preview(self, asset, autoplay):
        path = asset_path(asset)
        proxy = asset.get('proxy_path')
        kind = asset_kind(asset)
        self.notice_row.hide()
        if not path:
            self.player.stop_media()
            self.player.screen.set_text("No file is recorded for this asset.")
            return
        missing = asset.get('_missing')
        if missing is None:
            missing = not os.path.exists(path)
            asset['_missing'] = missing
        if missing:
            self.player.stop_media()
            self.player.screen.set_text("File not found\nThe source may have moved or been renamed.")
            self._set(self.lbl_path, wrappable(path), "bad")
            self.btn_open_external.hide()
            self.btn_copy_missing.show()
            self.notice_row.show()
            return
        if kind == "RAW" or not can_preview(asset):
            self.player.stop_media()
            self.player.screen.set_text("No preview for camera raw files" if kind == "RAW"
                                        else "No preview for this kind of file")
            self.btn_open_external.show()
            self.btn_copy_missing.hide()
            self.notice_row.show()
            return
        target = proxy if (proxy and os.path.exists(proxy)) else path
        self.player._pending_autoplay = bool(autoplay)
        # The original carries the sound; a proxy is made without it.
        self.player.load(target, audio_source=path)

    # ------------------------------------------------------------- actions
    def toggle_play(self):
        self.player.toggle_play()

    def copy_path(self):
        path = asset_path(self.current_asset)
        if path:
            QApplication.clipboard().setText(path)

    def _reveal(self):
        from .gallery import reveal_in_explorer
        reveal_in_explorer(asset_path(self.current_asset))

    def _open_externally(self):
        path = asset_path(self.current_asset)
        if path and os.path.exists(path):
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    def _favorite_clicked(self):
        if self.current_asset:
            self.favorite_toggled.emit(self.current_asset, self.btn_favorite.isChecked())

    def _pick_clicked(self):
        if self.current_asset:
            self.pick_toggled.emit(self.current_asset, self.btn_pick.isChecked())

    def set_visible(self, visible):
        super().setVisible(visible)

    def cleanup(self):
        if self.player:
            self.player.close()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.set_compact_mode(self.width() < 320)

    def set_compact_mode(self, compact: bool):
        """Reduce vertical pressure for narrow inspector widths."""
        self.player.setMinimumHeight(200 if compact else 240)
        self.player.setMaximumHeight(400 if compact else 600)
