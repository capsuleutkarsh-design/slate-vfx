"""
The Stock Viewer's list of assets, and how a card and a row are drawn.

One model serves both views: the grid reads column 0 (its card delegate draws
the thumbnail, a type badge, the length, the resolution class, a favourite
star and a "missing" mark), and the List view is a real table over the same
rows with Name, Type, Resolution, Length, Size, Added and Category columns
(MED-049). Both views share one selection.

Pictures load on a background thread. An asset whose thumbnail was never made
- the ingest's attempt failed, or it came in through an import - asks for one
once (thumbnail_needed); the Stock Viewer makes it in the background and saves
it, so "No Preview" is not forever (MED-030).
"""

import logging
from collections import OrderedDict
from pathlib import Path

from PySide6.QtCore import (
    Qt, QAbstractTableModel, QModelIndex, QSize, Signal, QRect, QRectF, QThread, QMutex,
    QWaitCondition, QUrl, QMimeData, QEvent, QPointF,
)
from PySide6.QtGui import (
    QPixmap, QColor, QPainter, QPen, QImage, QImageReader, QPainterPath, QIcon, QFont,
)
from PySide6.QtWidgets import QStyledItemDelegate, QStyle

from slate.core.infra.task_registry import task_registry
from slate.core.infra.gate import Gate
from slate.utils.media_capabilities import is_image, is_video

# Roles beyond Qt's own.
THUMB_ROLE = Qt.ItemDataRole.UserRole + 1        # QPixmap for the grid card
HoverRole = Qt.ItemDataRole.UserRole + 2
HoverPercentRole = Qt.ItemDataRole.UserRole + 3
SORT_ROLE = Qt.ItemDataRole.UserRole + 4         # a value the column sorts by

COLUMNS = ("Name", "Type", "Resolution", "Length", "Size", "Added", "Category")
# Which server-side sort a header click asks for (None = this column cannot be
# sorted in the database, so its header is not clickable for sorting).
COLUMN_SORTS = {0: ("name", "name_desc"), 1: ("type", "type_desc"), 2: None, 3: None,
                4: ("size_asc", "size"), 5: ("oldest", "newest"), 6: ("category", "category_desc")}

RAW_SUFFIXES = {".r3d", ".ari"}
QT_STILLS = {".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp", ".tif", ".tiff"}
DASH = "—"


# ------------------------------------------------------------------ facts

def _meta(asset):
    meta = asset.get("metadata") if isinstance(asset, dict) else None
    if isinstance(meta, str):
        import json
        try:
            meta = json.loads(meta) if meta else {}
        except ValueError:
            meta = {}
    return meta if isinstance(meta, dict) else {}


def asset_path(asset) -> str:
    return str((asset or {}).get("path") or (asset or {}).get("file_path") or "")


def asset_kind(asset) -> str:
    """SEQ, MOV, IMG, RAW or FILE - what the type badge says."""
    if not asset:
        return "FILE"
    if asset.get("is_sequence"):
        return "SEQ"
    suffix = Path(asset_path(asset)).suffix.lower()
    if suffix in RAW_SUFFIXES:
        return "RAW"
    if is_video(suffix):
        return "MOV"
    if is_image(suffix):
        return "IMG"
    return "FILE"


KIND_NAMES = {"SEQ": "Image sequence", "MOV": "Movie", "IMG": "Still",
              "RAW": "Camera raw", "FILE": "File"}


def timecode(seconds: float, fps: float) -> str:
    """h:mm:ss:ff when the rate is known, m:ss otherwise."""
    try:
        seconds = max(0.0, float(seconds or 0))
        fps = float(fps or 0)
    except (TypeError, ValueError):
        return DASH
    if fps > 0:
        total = int(round(seconds * fps))
        rate = max(1, int(round(fps)))
        frames = total % rate
        whole = total // rate
        return f"{whole // 3600}:{(whole // 60) % 60:02d}:{whole % 60:02d}:{frames:02d}"
    whole = int(round(seconds))
    if whole >= 3600:
        return f"{whole // 3600}:{(whole // 60) % 60:02d}:{whole % 60:02d}"
    return f"{whole // 60}:{whole % 60:02d}"


def length_text(asset, compact=False) -> str:
    """'1001-1024 (24 f)' for a sequence, a duration for a movie, a dash for a still."""
    meta = _meta(asset)
    if asset.get("is_sequence"):
        count = int(asset.get("frame_count") or meta.get("frame_count") or 0)
        first = int(asset.get("frame_first") or meta.get("frame_first") or 0)
        last = int(asset.get("frame_last") or meta.get("frame_last") or 0)
        if compact:
            return f"{count} f" if count else ""
        if count:
            return f"{first}–{last} ({count} f)" if last else f"{count} f"
        return DASH
    if meta.get("is_still") or asset_kind(asset) == "IMG":
        return "" if compact else DASH
    duration = meta.get("duration_sec") or meta.get("duration") or 0
    if not duration:
        return "" if compact else DASH
    if compact:
        whole = int(round(float(duration)))
        return f"{whole // 60}:{whole % 60:02d}"
    return timecode(duration, meta.get("fps") or 0)


def resolution_text(asset) -> str:
    meta = _meta(asset)
    w, h = meta.get("width") or meta.get("res_w"), meta.get("height") or meta.get("res_h")
    try:
        if int(w) > 0 and int(h) > 0:
            return f"{int(w)} × {int(h)}"
    except (TypeError, ValueError):
        pass
    return DASH


def resolution_badge(asset) -> str:
    from slate.core.domain.stock_search import resolution_class
    meta = _meta(asset)
    return resolution_class(meta.get("width"), meta.get("height"))


def size_text(size) -> str:
    try:
        size = int(size or 0)
    except (TypeError, ValueError):
        return DASH
    if size <= 0:
        return DASH
    for unit in ("bytes", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size} {unit}" if unit == "bytes" else f"{size:.1f} {unit}"
        size /= 1024.0
    return DASH


def added_text(asset) -> str:
    from slate.core.domain.dates import format_datetime
    return format_datetime(asset.get("ingest_date"), empty=DASH) if asset else DASH


def display_name(asset) -> str:
    """The name to show: never 'Unknown' (MED-072)."""
    if not asset:
        return DASH
    return (asset.get("name") or asset.get("display_name") or asset.get("file_name")
            or Path(asset_path(asset)).name or DASH)


def tooltip_text(asset) -> str:
    """Full name, kind, resolution, length and path - truncated cards were unreadable (MED-044)."""
    if not asset:
        return ""
    lines = [display_name(asset), KIND_NAMES.get(asset_kind(asset), "File")]
    res = resolution_text(asset)
    if res != DASH:
        lines[-1] += f" · {res}"
    length = length_text(asset)
    if length not in (DASH, ""):
        lines[-1] += f" · {length}"
    if asset.get("_missing"):
        lines.append("File not found - the source may have moved.")
    lines.append(asset_path(asset))
    return "\n".join(lines)


def badge_labels(asset) -> dict:
    """What the card shows over its thumbnail: kind, length, resolution, missing, pick."""
    return {
        "kind": asset_kind(asset),
        "length": length_text(asset, compact=True),
        "resolution": resolution_badge(asset),
        "missing": "Missing" if asset.get("_missing") else "",
        "pick": "Pick" if asset.get("is_pick") else "",
        "favorite": bool(asset.get("is_favorite")),
    }


def can_preview(asset) -> bool:
    return asset_kind(asset) in ("SEQ", "MOV", "IMG")


# ------------------------------------------------------------ thumbnails

class ThumbnailLoader(QThread):
    """Background thread to load thumbnails to avoid UI freeze. Uses LIFO Stack."""
    image_loaded = Signal(str, QImage) # path, QImage (Thread Safe)

    def __init__(self):
        super().__init__()
        self.queue = [] # LIFO Stack
        self.running = True
        self.mutex = QMutex()
        self.cond = QWaitCondition()
        self.processed = set()
        self.max_queue_size = 200 # Avoid memory bloat if user scrolls fast
        self.task_info = task_registry.register_task(
            name="Thumbnail Loader",
            description="Idle"
        )
        self.task_info.cancel_hook = self.stop

    def run(self):
        while self.running and not self.isInterruptionRequested():
            self.mutex.lock()
            if not self.queue:
                task_registry.update_progress(self.task_info.task_id, 100, "Idle")
                self.cond.wait(self.mutex)

            if not self.running or self.isInterruptionRequested():
                self.mutex.unlock()
                break

            # LIFO: Pop from end
            if self.queue:
                path = self.queue.pop()
                task_registry.update_progress(self.task_info.task_id, 0, f"Loading: {Path(path).name} ({len(self.queue)} remaining)")
            else:
                self.mutex.unlock()
                continue

            self.mutex.unlock()

            if not Path(path).exists():
                logging.debug(f"Thumbnail Missing on Disk: {path}")
                self.mutex.lock()
                self.processed.discard(path)
                self.mutex.unlock()
                continue

            try:
                reader = QImageReader(path)
                reader.setAutoDetectImageFormat(True)
                if reader.canRead():
                    orig_size = reader.size()
                    if orig_size.isValid() and orig_size.width() > 0:
                        target_w = 360
                        if orig_size.width() > target_w:
                            new_h = int(target_w * (orig_size.height() / orig_size.width()))
                            reader.setScaledSize(QSize(target_w, new_h))
                    image = reader.read()
                    if not image.isNull():
                        self.image_loaded.emit(path, image)
            except Exception as e:
                logging.exception(f"Thumbnail load error {path}: {e}")
            finally:
                # Always remove from processed so it can be re-requested if evicted from cache
                self.mutex.lock()
                self.processed.discard(path)
                self.mutex.unlock()

    def request_image(self, path):
        self.mutex.lock()
        try:
            if path not in self.processed:
                if len(self.queue) >= self.max_queue_size:
                    dropped = self.queue[0:50]
                    del self.queue[0:50]
                    for p in dropped:
                        self.processed.discard(p)
                if path in self.queue:
                    self.queue.remove(path)
                self.queue.append(path)
                self.processed.add(path)
                self.cond.wakeOne()
        finally:
            self.mutex.unlock()

    def clear_processed(self):
        """Thread-safe clear for processed request tracking."""
        self.mutex.lock()
        try:
            self.processed.clear()
        finally:
            self.mutex.unlock()

    def stop(self):
        """Stop the loader thread gracefully without force-terminate."""
        if not self.isRunning():
            return
        self.running = False
        self.requestInterruption()
        task_registry.finish_task(self.task_info.task_id)
        self.mutex.lock()
        self.queue.clear()
        self.cond.wakeAll()
        self.mutex.unlock()
        if not self.wait(5000):
            logging.error("ThumbnailLoader did not stop gracefully within timeout.")


# --------------------------------------------------------------- the model

class StockModel(QAbstractTableModel):
    # An asset with no thumbnail on record, shown for the first time.
    thumbnail_needed = Signal(dict)

    def __init__(self, assets=None, parent=None):
        super().__init__(parent)
        self.assets = assets or []
        self.icon_cache = OrderedDict()  # LRU cache: oldest items evicted first
        self.MAX_CACHE_SIZE = 500
        self._asset_map = {}
        self._id_map = {}
        self._asked_for_thumbs = set()
        self._rebuild_map()

        self.loader = ThumbnailLoader()
        self.loader.image_loaded.connect(self.on_image_loaded)
        self.loader.start()
        self.destroyed.connect(lambda *_: self.cleanup())

    # ---- shape
    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.assets)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(COLUMNS)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            if 0 <= section < len(COLUMNS):
                return COLUMNS[section]
        return super().headerData(section, orientation, role)

    # ---- data
    def _pixmap_for(self, asset):
        thumb_path = asset.get('thumb_path')
        if thumb_path and thumb_path in self.icon_cache:
            self.icon_cache.move_to_end(thumb_path)
            return self.icon_cache[thumb_path]
        path = asset_path(asset)
        if path and path in self.icon_cache:
            self.icon_cache.move_to_end(path)
            return self.icon_cache[path]
        if asset.get('_missing'):
            return None
        if thumb_path:
            self.loader.request_image(thumb_path)
            return None
        if asset.get('status') in ('pending', 'ingesting'):
            # Its thumbnail is seconds away; decoding the full-size source off
            # the share for every card at once is what brought a large ingest
            # to its knees.
            return None
        key = str(asset.get('id') or path)
        if path and can_preview(asset) and key not in self._asked_for_thumbs:
            self._asked_for_thumbs.add(key)
            self.thumbnail_needed.emit(dict(asset))
        # Meanwhile a still Qt can read is shown from the source itself.
        if path and Path(path).suffix.lower() in QT_STILLS and not asset.get('is_sequence'):
            self.loader.request_image(path)
        return None

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or index.row() >= len(self.assets):
            return None
        asset = self.assets[index.row()]
        column = index.column()

        if role == Qt.ItemDataRole.UserRole:
            return asset
        if role == Qt.ItemDataRole.ToolTipRole:
            return tooltip_text(asset)
        if role == THUMB_ROLE:
            return self._pixmap_for(asset)
        if role == Qt.ItemDataRole.DecorationRole:
            if column != 0:
                return None
            pixmap = self._pixmap_for(asset)
            return QIcon(pixmap) if pixmap is not None else None
        if role == Qt.ItemDataRole.ForegroundRole and asset.get('_missing'):
            return QColor(Gate.BAD)
        if role in (Qt.ItemDataRole.DisplayRole, SORT_ROLE):
            if column == 0:
                return display_name(asset)
            if column == 1:
                return KIND_NAMES.get(asset_kind(asset), "File")
            if column == 2:
                return resolution_text(asset)
            if column == 3:
                return length_text(asset)
            if column == 4:
                return size_text(asset.get('file_size'))
            if column == 5:
                return added_text(asset)
            if column == 6:
                return asset.get('category') or DASH
        if role == Qt.ItemDataRole.TextAlignmentRole and column in (4,):
            return int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        return None

    # ---- changes
    def load_data(self, new_assets):
        self.beginResetModel()
        self.assets = list(new_assets or [])
        self._rebuild_map()
        self.icon_cache.clear()
        if self.loader:
            self.loader.clear_processed()
        self.endResetModel()

    def _row_of(self, asset):
        target_id = asset.get('id')
        row = self._id_map.get(str(target_id)) if target_id is not None else None
        if row is None:
            path = asset_path(asset)
            row = self._asset_map.get(path) if path else None
        if row is not None and row < len(self.assets):
            return row
        return None

    def _changed(self, row):
        self.dataChanged.emit(self.index(row, 0), self.index(row, len(COLUMNS) - 1))

    def update_item(self, updated_asset):
        """Updates a single item in memory and refreshes the views."""
        row = self._row_of(updated_asset)
        if row is None:
            return False
        current = self.assets[row]
        same = (str(current.get('id')) == str(updated_asset.get('id'))
                or asset_path(current) == asset_path(updated_asset))
        if not same:
            return False
        current.update(updated_asset)
        self._update_map_entry(row, current)
        self._changed(row)
        return True

    def upsert_assets(self, assets):
        """
        Add new assets, refresh the ones already listed (matched by path).

        Re-ingesting files appended a second card for each one until a manual
        Refresh (MED-023).
        """
        fresh = []
        for asset in assets or []:
            row = self._asset_map.get(asset_path(asset)) if asset_path(asset) else None
            if row is not None and row < len(self.assets):
                keep_id = self.assets[row].get('id')
                self.assets[row].update(asset)
                if keep_id and str(keep_id).isdigit():
                    # Keep the database id the list was loaded with.
                    self.assets[row]['id'] = keep_id
                self._update_map_entry(row, self.assets[row])
                self._changed(row)
            else:
                fresh.append(asset)
        if fresh:
            self.add_assets(fresh)
        return len(fresh)

    def clear_assets(self):
        """Clear all assets from the model."""
        self.beginResetModel()
        self.assets = []
        self._asset_map = {}
        self._id_map = {}
        self.endResetModel()

    def add_assets(self, new_assets):
        if not new_assets:
            return
        start = len(self.assets)
        self.beginInsertRows(QModelIndex(), start, start + len(new_assets) - 1)
        self.assets.extend(new_assets)
        for i, asset in enumerate(new_assets):
            self._update_map_entry(start + i, asset)
        self.endInsertRows()

    def remove_assets(self, assets_to_remove):
        if not assets_to_remove:
            return
        rows = sorted({r for r in (self._row_of(a) for a in assets_to_remove) if r is not None},
                      reverse=True)
        for row in rows:
            self.beginRemoveRows(QModelIndex(), row, row)
            del self.assets[row]
            self.endRemoveRows()
        self._rebuild_map()

    def set_missing(self, paths):
        """Mark the assets whose source files are not on disk (checked off the UI thread)."""
        paths = set(paths or [])
        for row, asset in enumerate(self.assets):
            missing = asset_path(asset) in paths
            if bool(asset.get('_missing')) != missing:
                asset['_missing'] = missing
                self._changed(row)

    def _rebuild_map(self):
        self._asset_map = {}
        self._id_map = {}
        for i, asset in enumerate(self.assets):
            self._update_map_entry(i, asset)

    def _update_map_entry(self, index, asset):
        p = asset.get('thumb_path')
        if p:
            self._asset_map[p] = index
        p2 = asset_path(asset)
        if p2:
            self._asset_map[p2] = index
        aid = asset.get('id')
        if aid:
            self._id_map[str(aid)] = index

    def on_image_loaded(self, path, image):
        try:
            pixmap = QPixmap.fromImage(image)
            if pixmap.isNull():
                return
            if len(self.icon_cache) > self.MAX_CACHE_SIZE:
                self.icon_cache.popitem(last=False)
            self.icon_cache[path] = pixmap
            row = self._asset_map.get(path)
            if row is not None and row < len(self.assets):
                idx = self.index(row, 0)
                self.dataChanged.emit(idx, idx, [Qt.ItemDataRole.DecorationRole, THUMB_ROLE])
        except Exception as e:
            logging.exception(f"Error processing loaded image {path}: {e}")

    def clear(self):
        self.beginResetModel()
        self.assets = []
        self._asset_map = {}
        self._id_map = {}
        self.icon_cache = OrderedDict()
        if self.loader:
            self.loader.clear_processed()
        self.endResetModel()

    def flags(self, index):
        default_flags = super().flags(index)
        if index.isValid():
            return default_flags | Qt.ItemFlag.ItemIsDragEnabled
        return default_flags

    def mimeData(self, indexes):
        mime_data = QMimeData()
        urls, paths_list, seen = [], [], set()
        for index in indexes:
            if not index.isValid() or index.row() in seen:
                continue
            seen.add(index.row())
            path = asset_path(self.assets[index.row()])
            if path:
                try:
                    abs_path = str(Path(path).absolute())
                    urls.append(QUrl.fromLocalFile(abs_path))
                    paths_list.append(abs_path)
                except (TypeError, ValueError, OSError) as e:
                    logging.debug(f"Skipping invalid drag path '{path}': {e}")
        if urls:
            mime_data.setUrls(urls)
            mime_data.setText("\n".join(paths_list))
            return mime_data
        return None

    def cleanup(self):
        if hasattr(self, "loader") and self.loader:
            self.loader.stop()
            self.loader.deleteLater()
            self.loader = None

    def __del__(self):
        try:
            self.cleanup()
        except Exception as e:
            logging.debug(f"StockModel cleanup during __del__ failed: {e}")


# --------------------------------------------------------------- the card

def _star_path(rect: QRectF) -> QPainterPath:
    import math
    cx, cy = rect.center().x(), rect.center().y()
    outer = min(rect.width(), rect.height()) / 2.0
    inner = outer * 0.45
    path = QPainterPath()
    for i in range(10):
        radius = outer if i % 2 == 0 else inner
        angle = -math.pi / 2 + i * math.pi / 5
        point = QPointF(cx + radius * math.cos(angle), cy + radius * math.sin(angle))
        if i == 0:
            path.moveTo(point)
        else:
            path.lineTo(point)
    path.closeSubpath()
    return path


class StockDelegate(QStyledItemDelegate):
    """A card: thumbnail with badges, name underneath, a favourite star."""

    favorite_clicked = Signal(QModelIndex)

    # Badges sit on the picture, so they use a fixed dark wash and light text
    # in every theme - like a player's own on-screen display.
    BADGE_BG = QColor(0, 0, 0, 165)
    BADGE_TEXT = QColor(245, 245, 245)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.padding = 5
        self.thumb_height = 100
        self.thumb_width = 160
        self.text_height = 20
        self.allow_favorites = True

    def sizeHint(self, option, index):
        return QSize(self.thumb_width + (self.padding * 2) + 4,
                     self.thumb_height + self.text_height + (self.padding * 2) + 4)

    def _card_rect(self, option_rect):
        return option_rect.adjusted(2, 2, -2, -2)

    def _thumb_rect(self, card_rect):
        return QRect(card_rect.x() + self.padding, card_rect.y() + self.padding,
                     card_rect.width() - self.padding * 2, self.thumb_height)

    def star_rect(self, option_rect) -> QRect:
        thumb = self._thumb_rect(self._card_rect(option_rect))
        return QRect(thumb.right() - 26, thumb.top() + 4, 22, 22)

    def _badge(self, painter, text, anchor_rect, corner, fill=None, colour=None):
        if not text:
            return
        font = QFont(painter.font())
        font.setPixelSize(10)
        font.setBold(True)
        painter.setFont(font)
        metrics = painter.fontMetrics()
        w = metrics.horizontalAdvance(text) + 10
        h = metrics.height() + 2
        x = anchor_rect.left() + 4 if "left" in corner else anchor_rect.right() - w - 4
        y = anchor_rect.top() + 4 if "top" in corner else anchor_rect.bottom() - h - 4
        rect = QRect(x, y, w, h)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(fill or self.BADGE_BG)
        painter.drawRoundedRect(rect, 3, 3)
        painter.setPen(colour or self.BADGE_TEXT)
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)

    def paint(self, painter, option, index):
        if not index.isValid():
            return
        asset = index.data(Qt.ItemDataRole.UserRole) or {}
        name = display_name(asset)
        pixmap = index.data(THUMB_ROLE)
        is_selected = bool(option.state & QStyle.StateFlag.State_Selected)
        is_hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        card_rect = self._card_rect(option.rect)
        if is_selected:
            painter.setBrush(QColor(Gate.ACCENT_SURFACE))
            painter.setPen(QPen(QColor(Gate.ACCENT), 2))
        elif is_hovered:
            painter.setBrush(QColor(Gate.RAISED_HI))
            painter.setPen(QPen(QColor(Gate.LINE), 1))
        else:
            painter.setBrush(QColor(Gate.RAISED))
            painter.setPen(QPen(QColor(Gate.RAISED_HI), 1))
        painter.drawRoundedRect(card_rect, 6, 6)

        thumb_rect = self._thumb_rect(card_rect)
        if pixmap:
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
            rect_ratio = thumb_rect.width() / max(1, thumb_rect.height())
            pix_ratio = pixmap.width() / pixmap.height() if pixmap.height() > 0 else 1
            if pix_ratio > rect_ratio:
                new_h = int(thumb_rect.width() / pix_ratio)
                target = QRect(thumb_rect.x(), thumb_rect.y() + (thumb_rect.height() - new_h) // 2,
                               thumb_rect.width(), new_h)
            else:
                new_w = int(thumb_rect.height() * pix_ratio)
                target = QRect(thumb_rect.x() + (thumb_rect.width() - new_w) // 2, thumb_rect.y(),
                               new_w, thumb_rect.height())
            painter.save()
            clip_path = QPainterPath()
            clip_path.addRoundedRect(QRectF(thumb_rect), 4, 4)
            painter.setClipPath(clip_path)
            painter.drawPixmap(target, pixmap)
            painter.restore()
        else:
            painter.setBrush(QColor(Gate.PANEL))
            painter.setPen(QColor(Gate.LINE))
            painter.drawRoundedRect(thumb_rect, 4, 4)
            status = asset.get('status', 'ready')
            text, colour = "No preview", QColor(Gate.TEXT_DIM)
            if asset.get('_missing'):
                text, colour = "File not found", QColor(Gate.BAD)
            elif status == 'ingesting':
                text, colour = "Analysing…", QColor(Gate.ACCENT)
            elif status == 'corrupt':
                text, colour = "Could not read", QColor(Gate.BAD)
            elif asset_kind(asset) == "RAW":
                text = "Camera raw"
            painter.setPen(colour)
            painter.drawText(thumb_rect, Qt.AlignmentFlag.AlignCenter, text)

        badges = badge_labels(asset)
        self._badge(painter, badges["kind"], thumb_rect, "top-left")
        self._badge(painter, badges["length"], thumb_rect, "bottom-left")
        self._badge(painter, badges["resolution"], thumb_rect, "bottom-right")
        if badges["missing"]:
            self._badge(painter, badges["missing"], thumb_rect, "top-right" if not self.allow_favorites
                        else "bottom-right", fill=QColor(Gate.BAD), colour=QColor(Gate.TEXT_ON_BAD))
        if badges["pick"]:
            self._badge(painter, badges["pick"], QRect(thumb_rect.x(), thumb_rect.y() + 18,
                                                       thumb_rect.width(), thumb_rect.height() - 18),
                        "top-left", fill=QColor(Gate.ACCENT), colour=QColor(Gate.TEXT_ON_ACCENT))

        if self.allow_favorites and (badges["favorite"] or is_hovered):
            star = QRectF(self.star_rect(option.rect)).adjusted(3, 3, -3, -3)
            path = _star_path(star)
            painter.setPen(QPen(self.BADGE_BG, 2))
            painter.setBrush(QColor(Gate.WARN) if badges["favorite"] else Qt.BrushStyle.NoBrush)
            painter.drawPath(path)
            painter.setPen(QPen(QColor(Gate.WARN) if badges["favorite"] else self.BADGE_TEXT, 1.2))
            painter.drawPath(path)

        text_rect = QRect(card_rect.x() + self.padding,
                          card_rect.y() + self.padding + self.thumb_height + 2,
                          card_rect.width() - self.padding * 2, self.text_height)
        painter.setPen(QColor(Gate.BAD) if asset.get('_missing') else QColor(Gate.TEXT))
        painter.setFont(option.font)
        elided_text = option.fontMetrics.elidedText(name, Qt.TextElideMode.ElideMiddle,
                                                    text_rect.width())
        painter.drawText(text_rect, Qt.AlignmentFlag.AlignCenter, elided_text)
        painter.restore()

    def editorEvent(self, event, model, option, index):
        """A click on the star toggles the favourite, without changing the selection."""
        if (self.allow_favorites and event.type() == QEvent.Type.MouseButtonRelease
                and event.button() == Qt.MouseButton.LeftButton
                and self.star_rect(option.rect).contains(event.position().toPoint())):
            self.favorite_clicked.emit(index)
            return True
        if (self.allow_favorites and event.type() == QEvent.Type.MouseButtonPress
                and self.star_rect(option.rect).contains(event.position().toPoint())):
            return True
        return super().editorEvent(event, model, option, index)
