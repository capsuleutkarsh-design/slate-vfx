"""
Stock Browser Helper Widgets.

Extracted from stock_browser_tab.py for better code organization.

Contains reusable UI widgets:
- PyToggle: Animated toggle switch widget
- AssetSortFilterProxyModel: what the gallery hides on screen
- AssetKeys: the gallery's keyboard (Space, Enter, Delete, Ctrl+D, player keys)
- DraggableListView: the card grid, with drag & drop of folders and assets
"""

from PySide6.QtWidgets import (
    QCheckBox, QListView, QAbstractItemView, QApplication
)
from PySide6.QtCore import (
    Qt, Signal, QMimeData, QUrl, QPropertyAnimation,
    QEasingCurve, Property, QPoint, QSortFilterProxyModel
)
from PySide6.QtGui import QPainter, QColor, QDrag, QKeySequence, QPen
from pathlib import Path
import logging
from slate.core.infra.gate import Gate
from slate.core.infra.stock_repository import ALL, FAVORITES, REMOVED, STUDIO_PICKS


class PyToggle(QCheckBox):
    """
    Animated toggle switch widget.

    The off track used to be the same colour as the panel it sat on, so only
    the knob showed - "a stray white dot" (MED-038). The track now has an edge
    in both states and the knob is drawn in the text colour on the off track.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(44, 24)
        self.setMaximumSize(44, 24)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._circle_position = 3
        self.animation = QPropertyAnimation(self, b"circle_position", self)
        self.animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.animation.setDuration(160)
        self.stateChanged.connect(self.start_transition)

    def start_transition(self, value):
        self.animation.stop()
        circle_size = self.height() - 6
        self.animation.setEndValue(self.width() - circle_size - 3 if value else 3)
        self.animation.start()

    def hitButton(self, pos: QPoint):
        return self.contentsRect().contains(pos)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        on = self.isChecked()
        enabled = self.isEnabled()
        track = QColor(Gate.ACCENT if on else Gate.RAISED_HI)
        edge = QColor(Gate.ACCENT if on else Gate.TEXT_DIM)
        if not enabled:
            track.setAlphaF(0.45)
            edge.setAlphaF(0.45)
        p.setBrush(track)
        p.setPen(QPen(edge, 1))
        radius = (self.height() - 1) / 2
        p.drawRoundedRect(0.5, 0.5, self.width() - 1, self.height() - 1, radius, radius)

        knob = QColor(Gate.TEXT_ON_ACCENT if on else Gate.TEXT)
        if not enabled:
            knob.setAlphaF(0.6)
        p.setBrush(knob)
        p.setPen(Qt.PenStyle.NoPen)
        circle_size = self.height() - 6
        y_pos = (self.height() - circle_size) / 2
        p.drawEllipse(self._circle_position, y_pos, circle_size, circle_size)
        p.end()

    def get_circle_position(self):
        return self._circle_position

    def set_circle_position(self, pos):
        self._circle_position = pos
        self.update()

    circle_position = Property(int, get_circle_position, set_circle_position)


class AssetSortFilterProxyModel(QSortFilterProxyModel):
    """
    What the gallery hides on screen.

    Searching, the category, the visual filter and the media type are all
    applied in the database now, with the paging, so the list and its total
    agree (MED-007). This only keeps the screen honest between reloads: an
    asset unstarred while "Favorites" is open, or a new one arriving from a
    running ingest that does not belong in the category being looked at.
    """

    VIDEO = {'.mov', '.mp4', '.avi', '.mkv', '.m4v', '.webm', '.mxf'}

    def __init__(self, parent=None):
        super().__init__(parent)
        self.filter_category = "All"
        self.filter_text = ""
        self.visual_tag_filter = "Any"
        self.media_type_filter = "All"

    def set_category(self, category):
        self.filter_category = category or "All"
        self.invalidateFilter()

    def set_text_filter(self, text):
        # Kept for callers; the search itself is the database's job.
        self.filter_text = (text or "").lower()

    def set_visual_tag_filter(self, tag):
        self.visual_tag_filter = tag or "Any"

    def set_media_type_filter(self, media_type):
        self.media_type_filter = media_type or "All"
        self.invalidateFilter()

    def filterAcceptsRow(self, source_row, source_parent):
        model = self.sourceModel()
        asset = model.data(model.index(source_row, 0, source_parent), Qt.ItemDataRole.UserRole)
        if not asset:
            return False
        if asset.get('_hidden'):
            return False

        category = self.filter_category
        if category == FAVORITES:
            return bool(asset.get('is_favorite'))
        if category == STUDIO_PICKS:
            return bool(asset.get('is_pick'))
        if category not in (ALL, REMOVED, "") and asset.get('category') != category:
            return False

        if self.media_type_filter != "All" and asset.get('status') == 'ingesting':
            suffix = Path(asset.get('path') or asset.get('file_path') or "").suffix.lower()
            is_movie = suffix in self.VIDEO
            if (self.media_type_filter == "Videos") != is_movie:
                return False
        return True


class AssetKeys:
    """The gallery keys, shared by the grid and the table (MED-033, MED-056, MED-117)."""

    PLAYER_KEYS = (Qt.Key.Key_J, Qt.Key.Key_K, Qt.Key.Key_L, Qt.Key.Key_F, Qt.Key.Key_M,
                   Qt.Key.Key_Home, Qt.Key.Key_End, Qt.Key.Key_Comma, Qt.Key.Key_Period)

    @staticmethod
    def handle(view, event) -> bool:
        key = event.key()
        mods = event.modifiers()
        if event.matches(QKeySequence.StandardKey.Copy):
            view.copy_requested.emit()
            return True
        if key == Qt.Key.Key_Space and not mods:
            view.preview_requested.emit()
            return True
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and not mods:
            view.play_requested.emit()
            return True
        if key == Qt.Key.Key_Delete and not mods:
            view.delete_requested.emit()
            return True
        if key == Qt.Key.Key_D and mods & Qt.KeyboardModifier.ControlModifier:
            view.favorite_requested.emit()
            return True
        if key in AssetKeys.PLAYER_KEYS and not mods:
            view.player_key.emit(event)
            return True
        return False


class DraggableListView(QListView):
    """List view with drag & drop support for folders and assets."""

    folders_dropped = Signal(list)
    files_dropped = Signal(list)
    preview_requested = Signal()      # Space: Quick Look
    play_requested = Signal()         # Enter: play / pause in the inspector
    delete_requested = Signal()       # Delete
    favorite_requested = Signal()     # Ctrl+D
    copy_requested = Signal()         # Ctrl+C
    player_key = Signal(object)       # J K L M F Home End , . for the inspector player
    zoom_step = Signal(int)           # Ctrl + wheel

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(False)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DragDrop)
        self.setDefaultDropAction(Qt.DropAction.CopyAction)
        self.viewport().setMouseTracking(True)
        self.setMouseTracking(True)
        # Whether dropping folders here starts an ingest for this person.
        self.accept_folders = True

    def _external(self, event) -> bool:
        """A drop from outside this view (a card dragged back onto the grid is not)."""
        return event.source() is not self and event.source() is not getattr(self, "_twin", None)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls() and self._external(event):
            event.acceptProposedAction()
        elif event.mimeData().hasUrls():
            event.ignore()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event):
        if event.mimeData().hasUrls() and self._external(event):
            event.setDropAction(Qt.DropAction.CopyAction)
            event.accept()
        elif event.mimeData().hasUrls():
            event.ignore()
        else:
            super().dragMoveEvent(event)

    def dropEvent(self, event):
        """
        Folders dropped from outside start an ingest.

        A card dragged and let go on the grid itself used to come back as a
        "drop folders, not files" warning (MED-043); drops from this view are
        ignored, and nothing is passed on to QListView, which would try to move
        the item.
        """
        if not event.mimeData().hasUrls() or not self._external(event):
            event.ignore()
            return
        paths = [u.toLocalFile() for u in event.mimeData().urls() if u.isLocalFile()]
        folders = [p for p in paths if Path(p).is_dir()]
        files = [p for p in paths if Path(p).is_file()]
        if folders:
            logging.info(f"Dropped folders: {folders}")
            self.folders_dropped.emit(folders)
        elif files:
            self.files_dropped.emit(files)
        event.acceptProposedAction()

    def startDrag(self, supportedActions):
        indexes = [i for i in self.selectedIndexes() if i.column() == 0]
        if not indexes:
            return
        mime_data = self.model().mimeData(indexes)
        if not mime_data:
            return
        drag = QDrag(self)
        drag.setMimeData(mime_data)
        from slate.gui.stock_model import THUMB_ROLE
        pixmap = self.model().data(indexes[0], THUMB_ROLE)
        if pixmap:
            drag.setPixmap(pixmap.scaled(100, 100, Qt.AspectRatioMode.KeepAspectRatio))
            drag.setHotSpot(QPoint(50, 50))
        drag.exec(Qt.DropAction.CopyAction)

    def keyPressEvent(self, event):
        if AssetKeys.handle(self, event):
            event.accept()
            return
        super().keyPressEvent(event)

    def wheelEvent(self, event):
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            step = 1 if event.angleDelta().y() > 0 else -1
            self.zoom_step.emit(step)
            event.accept()
            return
        super().wheelEvent(event)

    def copy_selection(self):
        """Copy the selected assets' paths: one per line, and as files (MED-018)."""
        copy_paths(self.selectedIndexes())


def copy_paths(indexes):
    urls, paths, seen = [], [], set()
    for idx in indexes:
        if idx.row() in seen:
            continue
        seen.add(idx.row())
        asset = idx.data(Qt.ItemDataRole.UserRole)
        if asset:
            path = asset.get('path') or asset.get('file_path')
            if path:
                norm_path = str(Path(path).absolute())
                paths.append(norm_path)
                urls.append(QUrl.fromLocalFile(norm_path))
    if not paths:
        logging.warning("Copy failed: No valid paths found in selection")
        return []
    mime = QMimeData()
    mime.setUrls(urls)
    mime.setText("\n".join(paths))
    QApplication.clipboard().setMimeData(mime)
    return paths
