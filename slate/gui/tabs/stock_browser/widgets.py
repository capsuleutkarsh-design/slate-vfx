"""
Stock Browser Helper Widgets.

Extracted from stock_browser_tab.py for better code organization.

Contains reusable UI widgets:
- PyToggle: Animated toggle switch widget
- AssetSortFilterProxyModel: Filtering logic for assets
- DraggableListView: List view with drag & drop support
"""

from PySide6.QtWidgets import (
    QCheckBox, QListView, QAbstractItemView, QApplication
)
from PySide6.QtCore import (
    Qt, Signal, QMimeData, QUrl, QPropertyAnimation, 
    QEasingCurve, Property, QPoint, QSortFilterProxyModel
)
from PySide6.QtGui import QPainter, QColor, QDrag
from pathlib import Path
import logging


class PyToggle(QCheckBox):
    """Animated toggle switch widget."""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(50, 28)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._bg_color = "#26262D"
        self._circle_color = "#E8E6E1"
        self._active_color = "#3EA8BF"
        self._circle_position = 3
        self.animation = QPropertyAnimation(self, b"circle_position", self)
        self.animation.setEasingCurve(QEasingCurve.OutBounce)
        self.animation.setDuration(300)
        self.stateChanged.connect(self.start_transition)
    
    def start_transition(self, value):
        self.animation.stop()
        circle_size = self.height() - 6
        if value:
            self.animation.setEndValue(self.width() - circle_size - 3)
        else:
            self.animation.setEndValue(3)
        self.animation.start()
    
    def hitButton(self, pos: QPoint):
        return self.contentsRect().contains(pos)
    
    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setBrush(QColor(self._active_color if self.isChecked() else self._bg_color))
        p.setPen(Qt.NoPen)
        radius = self.height() / 2
        p.drawRoundedRect(0, 0, self.width(), self.height(), radius, radius)
        
        p.setBrush(QColor(self._circle_color))
        circle_size = self.height() - 6
        # Center vertically
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
    """Proxy model for filtering assets by category, text, and visual tags."""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.filter_category = "All"
        self.filter_text = ""
        self.visual_tag_filter = "Any"
        self.media_type_filter = "All"

    def set_category(self, category):
        self.filter_category = category
        self.invalidateFilter()
    
    def set_text_filter(self, text):
        self.filter_text = text.lower()
        self.invalidateFilter()
        
    def set_visual_tag_filter(self, tag):
        self.visual_tag_filter = tag
        self.invalidateFilter()

    def set_media_type_filter(self, media_type):
        self.media_type_filter = media_type
        self.invalidateFilter()

    def filterAcceptsRow(self, source_row, source_parent):
        model = self.sourceModel()
        index = model.index(source_row, 0, source_parent)
        asset = model.data(index, Qt.ItemDataRole.UserRole)
        
        if not asset:
            return False

        # Category Filter
        if self.filter_category != "All":
            if self.filter_category == "Favorites":
                if not asset.get('_is_favorite', False):
                    return False
            elif asset.get('category') != self.filter_category:
                return False

        # Text Filter (Optimized)
        if self.filter_text:
            # Use pre-calculated search key (Name + Tags lowercased)
            search_key = asset.get('_search_key', '')
            if self.filter_text not in search_key:
                return False

        # Visual Tag Filter
        if self.visual_tag_filter != "Any":
            tags_str = str(asset.get('tags', ''))
            if self.visual_tag_filter not in tags_str:
                return False

        # Media Type Filter
        if self.media_type_filter != "All":
            path = asset.get('path') or asset.get('file_path') or ""
            suffix = Path(path).suffix.lower()
            
            video_ext = ['.mov', '.mp4', '.avi', '.mkv']
            image_ext = ['.jpg', '.png', '.exr', '.dpx', '.tif', '.tiff', '.bmp', '.gif', '.webp']
            
            if self.media_type_filter == "Videos":
                if suffix not in video_ext: return False
            elif self.media_type_filter == "Images":
                if suffix not in image_ext: return False

        return True


class DraggableListView(QListView):
    """List view with drag & drop support for folders and assets."""
    
    folders_dropped = Signal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)  # ENABLED
        self.setDropIndicatorShown(True)
        self.setDragDropMode(QAbstractItemView.DragDrop)  # Changed from DragOnly
        self.setDefaultDropAction(Qt.CopyAction)
        # Enable hover tracking for delegate hover effects
        self.viewport().setMouseTracking(True)
        self.setMouseTracking(True)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.accept()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event):
        if event.mimeData().hasUrls():
            event.setDropAction(Qt.CopyAction)
            event.accept()
        else:
            super().dragMoveEvent(event)

    def dropEvent(self, event):
        if event.mimeData().hasUrls():
            urls = event.mimeData().urls()
            paths = [u.toLocalFile() for u in urls if u.isLocalFile()]
            folders = [p for p in paths if Path(p).is_dir()]
            
            files = [p for p in paths if Path(p).is_file()]
            
            if folders:
                logging.info(f"Dropped folders: {folders}")
                self.folders_dropped.emit(folders)
                event.accept()
            elif files:
                from PySide6.QtWidgets import QMessageBox
                QMessageBox.warning(self, "Invalid Drop", "Please drop folders instead of individual files to ingest.")
                event.accept()
        
        super().dropEvent(event)

    def startDrag(self, supportedActions):
        logging.info("DraggableListView.startDrag triggered")
        indexes = self.selectedIndexes()
        if not indexes:
            return

        # Ensure we get mimeData from the correct model (proxy or source)
        mime_data = self.model().mimeData(indexes)
        
        if not mime_data:
            logging.warning("No mimeData returned from model")
            return

        drag = QDrag(self)
        drag.setMimeData(mime_data)
        
        # Optional: Set a pixmap for the drag
        if len(indexes) > 0:
            pixmap = self.model().data(indexes[0], Qt.ItemDataRole.DecorationRole)
            if pixmap:
                drag.setPixmap(pixmap.scaled(100, 100, Qt.KeepAspectRatio))
                drag.setHotSpot(QPoint(50, 50))
                
        drag.exec_(supportedActions)

    def keyPressEvent(self, event):
        from PySide6.QtGui import QKeySequence
        if event.matches(QKeySequence.Copy):
            self.copy_selection()
            event.accept()
        else:
            super().keyPressEvent(event)
            
    def copy_selection(self):
        indexes = self.selectedIndexes()
        if not indexes:
            logging.warning("Copy failed: No selection")
            return
        
        urls = []
        paths = []
        
        for idx in indexes:
            # QSortFilterProxyModel automatically forwards data() calls to source
            # So we can just grasp the asset dict directly from the view index
            asset = idx.data(Qt.ItemDataRole.UserRole)
            if asset:
                path = asset.get('path') or asset.get('file_path')
                if path:
                    # Windows path normalization just in case
                    norm_path = str(Path(path).absolute())
                    paths.append(norm_path)
                    urls.append(QUrl.fromLocalFile(norm_path))
        
        if paths:
            mime = QMimeData()
            mime.setUrls(urls)
            mime.setText("\\n".join(paths))
            QApplication.clipboard().setMimeData(mime)
            logging.info(f"Copied {len(paths)} items to clipboard: {paths[0]}...")
        else:
            logging.warning("Copy failed: No valid paths found in selection")
