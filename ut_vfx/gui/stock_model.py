import logging
from collections import OrderedDict
from pathlib import Path
from PySide6.QtCore import Qt, QAbstractListModel, QModelIndex, QSize, Signal, QRect, QThread, QMutex, QWaitCondition, QUrl, QMimeData
from PySide6.QtGui import QPixmap, QColor, QPainter, QPen, QImage, QImageReader, QPainterPath
from PySide6.QtWidgets import QStyledItemDelegate, QStyle

from ut_vfx.core.infra.task_registry import task_registry

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

            # Optimization: Check if file exists here
            if not Path(path).exists():
                logging.warning(f"Thumbnail Missing on Disk: {path}")
                self.mutex.lock()
                self.processed.discard(path)
                self.mutex.unlock()
                continue
            
            try:
                reader = QImageReader(path)
                # Auto-detect format
                reader.setAutoDetectImageFormat(True)
                
                if reader.canRead():
                    # Robust Size Check
                    orig_size = reader.size()
                    if orig_size.isValid() and orig_size.width() > 0:
                        # Only scale if really needed and safe
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
                # CRITICAL FIX: Ghosting
                # Always remove from processed so it can be re-requested if evicted from cache
                self.mutex.lock()
                self.processed.discard(path)
                self.mutex.unlock()

    def request_image(self, path):
        self.mutex.lock()
        try:
            if path not in self.processed:
                # Maintain Max Size: remove oldest items (from start of list) if full
                if len(self.queue) >= self.max_queue_size:
                    # Remove oldest requests (index 0)
                    # User is scrolling fast, old items are likely off-screen
                    dropped = self.queue[0:50] # Capture dropped items
                    del self.queue[0:50] # Bulk remove
                    
                    # CRITICAL FIX: Ghosting
                    # Remove dropped items from processed set so they can be re-requested
                    for p in dropped:
                        self.processed.discard(p)
                    
                # Avoid duplicates in queue (move to top/end if already exists)
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
        # Clear the queue to prevent processing during shutdown
        self.queue.clear()
        self.cond.wakeAll()
        self.mutex.unlock()
        
        # Wait with 5 second timeout
        if not self.wait(5000):  # 5 seconds
            logging.error("ThumbnailLoader did not stop gracefully within timeout.")
        else:
            logging.info("ThumbnailLoader stopped gracefully")

HoverRole = Qt.ItemDataRole.UserRole + 2
HoverPercentRole = Qt.ItemDataRole.UserRole + 3

class StockModel(QAbstractListModel):
    def __init__(self, assets=None, parent=None):
        super().__init__(parent)
        self.assets = assets or []
        self.icon_cache = OrderedDict()  # LRU cache: oldest items evicted first
        self.MAX_CACHE_SIZE = 500

        self._asset_map = {} # O(1) Lookup
        self._id_map = {} # ID Lookup
        self._rebuild_map()
        
        # Initialize background loader
        self.loader = ThumbnailLoader()
        self.loader.image_loaded.connect(self.on_image_loaded)
        self.loader.start()
        self.destroyed.connect(lambda *_: self.cleanup())

    def rowCount(self, parent=QModelIndex()):
        return len(self.assets)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        
        asset = self.assets[index.row()]
        
        if role == Qt.ItemDataRole.DisplayRole:
            return asset.get('name') or asset.get('file_name') or 'Unknown'
            
        elif role == Qt.ItemDataRole.UserRole: # Full Asset Data
            return asset
            
        elif role == Qt.ItemDataRole.DecorationRole: # Thumbnail
            thumb_path = asset.get('thumb_path')
            
            # Check Valid Cache First (Fastest)
            # We check both thumb_path AND source path in cache
             # 1. Try thumb path
            if thumb_path and thumb_path in self.icon_cache:
                self.icon_cache.move_to_end(thumb_path)  # LRU: mark as recently used
                return self.icon_cache[thumb_path]
            
            # 2. Try source path (Fallback cache key)
            path = asset.get('path') or asset.get('file_path')
            if path and path in self.icon_cache:
                self.icon_cache.move_to_end(path)  # LRU: mark as recently used
                return self.icon_cache[path]
            
            # If we have a valid thumb path but no cache, request load 
            if thumb_path:
                # DEBUG: Check if thumb exists
                # if not Path(thumb_path).exists():
                     # Limit log spam
                     # pass 
                self.loader.request_image(thumb_path)
                return None  
            
            # Fallback for Images: If no thumb, try using the source file directly
            if not thumb_path and path:
                 suffix = Path(path).suffix.lower()
                 if suffix in ['.png', '.jpg', '.jpeg', '.bmp', '.gif', '.webp', '.tif', '.tiff']:
                     # Request load using SOURCE path
                     self.loader.request_image(path)
                     return None
            return None 
            
        return None

    def load_data(self, new_assets):
        self.beginResetModel()
        self.assets = new_assets
        
        # Pre-calc cache for fast search
        for asset in self.assets:
            self._generate_cache(asset)
            
        self._rebuild_map()
        self.icon_cache.clear() # Clear cache on full reload to free mem
        self.loader.clear_processed() # Reset loader tracking
        self.endResetModel()

    def update_item(self, updated_asset):
        """Updates a single item in memory and refreshes View."""
        # O(1) Optimization: Use ID map
        target_id = updated_asset.get('id')
        row = None
        
        # 1. Try ID Map (Fastest and most reliable)
        if target_id:
             row = self._id_map.get(str(target_id))
        
        # 2. Try Path Map (Fallback) if ID retrieval failed
        if row is None:
            path = updated_asset.get('thumb_path') or updated_asset.get('path')
            row = self._asset_map.get(path)
        
        # 3. Validation: Verify row still matches target (in case of stale map)
        if row is not None and row < len(self.assets):
             current = self.assets[row]
             # Only update if IDs match or paths match
             if (str(current.get('id')) == str(target_id)) or (current.get('path') == updated_asset.get('path')):
                self.assets[row].update(updated_asset)
                self._generate_cache(self.assets[row]) # Update cache
                
                idx = self.index(row, 0)
                self.dataChanged.emit(idx, idx, [Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.DecorationRole, Qt.ItemDataRole.UserRole])
                
                # Update Maps with new paths if changed
                self._update_map_entry(row, self.assets[row])

    def clear_assets(self):
        """Clear all assets from the model."""
        self.beginResetModel()
        self.assets = []
        self._asset_map = {}
        self._id_map = {}
        self.endResetModel()

    def add_assets(self, new_assets):
        if not new_assets: return
        start = len(self.assets)
        self.beginInsertRows(QModelIndex(), start, start + len(new_assets) - 1)
        
        # Pre-calc cache
        for asset in new_assets:
            self._generate_cache(asset)
            
        self.assets.extend(new_assets)
        for i, asset in enumerate(new_assets):
            self._update_map_entry(start + i, asset)
        self.endInsertRows()
        
    def remove_assets(self, assets_to_remove):
        if not assets_to_remove: return
        
        # Identify indices to remove
        indices_to_remove = []
        for asset in assets_to_remove:
            target_id = str(asset.get('id', ''))
            row = self._id_map.get(target_id)
            if row is None:
                path = asset.get('thumb_path') or asset.get('path')
                row = self._asset_map.get(path)
            
            if row is not None and row not in indices_to_remove:
                indices_to_remove.append(row)
                
        # Remove from highest to lowest to preserve row indices
        indices_to_remove.sort(reverse=True)
        
        for row in indices_to_remove:
            self.beginRemoveRows(QModelIndex(), row, row)
            del self.assets[row]
            self.endRemoveRows()
            
        self._rebuild_map()
        
    def _generate_cache(self, asset):
        """Pre-calculate search strings and flags for O(1) filtering."""
        # Search Key
        name = (asset.get('name') or '').lower()
        tags = asset.get('tags', [])
        if isinstance(tags, list):
            tags_str = " ".join(str(t) for t in tags).lower()
        else:
            tags_str = str(tags).lower()
            
        # Add Folder Name to Search (User Request)
        path = asset.get('path') or asset.get('file_path') or ""
        folder_search = ""
        if path:
             try:
                 p = Path(path)
                 # Add parent folder name (e.g., "Dirtmap" from "X:/Assets/Dirtmap/file.jpg")
                 folder_search = f"{p.parent.name.lower()} {p.parent.parent.name.lower()}"
             except (TypeError, ValueError, OSError) as e:
                 logging.debug(f"Folder search key derivation failed for '{path}': {e}")
        
        # Combine all searchable text
        asset['_search_key'] = f"{name} {tags_str} {folder_search}"
        
        # Favorite Flag
        # Handle string lists "tag1, tag2" vs list objects ["tag1"]
        asset['_is_favorite'] = False
        if isinstance(tags, list):
             if "Favorite" in tags: asset['_is_favorite'] = True
        elif isinstance(tags, str):
             if "Favorite" in tags: asset['_is_favorite'] = True
        
    def _rebuild_map(self):
        """Builds O(1) lookups for Paths and IDs."""
        self._asset_map = {}
        self._id_map = {}
        for i, asset in enumerate(self.assets):
            self._update_map_entry(i, asset)

    def _update_map_entry(self, index, asset):
        """Helper to register an asset in lookup maps."""
        # Map Paths
        p = asset.get('thumb_path')
        if p: self._asset_map[p] = index
        p2 = asset.get('path') or asset.get('file_path')
        if p2 and p2 not in self._asset_map: self._asset_map[p2] = index
        
        # Map ID
        aid = asset.get('id')
        if aid: self._id_map[str(aid)] = index

    def on_image_loaded(self, path, image):
        """Handle loaded image from background thread with error handling."""
        try:
            # Convert QImage to QPixmap on Main Thread (Fast now because image is small)
            pixmap = QPixmap.fromImage(image)
            
            if pixmap.isNull():
                logging.warning(f"Failed to convert image to pixmap: {path}")
                return
            
            # LRU eviction: remove least-recently-used entry
            if len(self.icon_cache) > self.MAX_CACHE_SIZE:
                 self.icon_cache.popitem(last=False)  # Remove oldest (LRU)
                 
            self.icon_cache[path] = pixmap

            # Optimized O(1) Lookup
            row = self._asset_map.get(path)
            if row is not None and row < len(self.assets):
                 idx = self.index(row, 0)
                 self.dataChanged.emit(idx, idx, [Qt.ItemDataRole.DecorationRole])
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
        urls = []
        paths_list = []
        
        for index in indexes:
            if index.isValid():
                asset = self.assets[index.row()]
                path = asset.get('path') or asset.get('file_path')
                if path:
                    try:
                        p_obj = Path(path)
                        abs_path = str(p_obj.absolute())
                        url = QUrl.fromLocalFile(abs_path)
                        urls.append(url)
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
        """Best-effort cleanup when model is garbage collected."""
        try:
            self.cleanup()
        except Exception as e:
            logging.debug(f"StockModel cleanup during __del__ failed: {e}")

class StockDelegate(QStyledItemDelegate):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.padding = 5
        self.thumb_height = 100
        self.thumb_width = 160
        self.text_height = 20

    def sizeHint(self, option, index):
        return QSize(self.thumb_width + (self.padding*2), self.thumb_height + self.text_height + (self.padding*2))

    def paint(self, painter, option, index):
        if not index.isValid(): return

        name = index.data(Qt.ItemDataRole.DisplayRole)
        pixmap = index.data(Qt.ItemDataRole.DecorationRole)
        is_selected = option.state & QStyle.StateFlag.State_Selected
        is_hovered = option.state & QStyle.StateFlag.State_MouseOver
        rect = option.rect
        
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        
        # Card Background — rounded rect with subtle fill
        card_rect = rect.adjusted(2, 2, -2, -2)  # Inset for spacing between cards
        
        if is_selected:
            painter.setBrush(QColor("#16323A"))  # Dark blue tint
            painter.setPen(QPen(QColor("#3EA8BF"), 2))  # Accent border
        elif is_hovered:
            painter.setBrush(QColor("#26262D"))  # Subtle hover lift
            painter.setPen(QPen(QColor("#2C2C34"), 1))
        else:
            painter.setBrush(QColor("#1D1D22"))  # Base card color
            painter.setPen(QPen(QColor("#26262D"), 1))
        
        painter.drawRoundedRect(card_rect, 6, 6)
        
        # Thumbnail Area (inside card)
        thumb_rect = QRect(card_rect.x() + self.padding, card_rect.y() + self.padding, 
                          card_rect.width() - self.padding * 2, self.thumb_height)
        
        if pixmap:
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
            
            # Aspect Fit logic
            rect_ratio = thumb_rect.width() / thumb_rect.height()
            pix_ratio = pixmap.width() / pixmap.height() if pixmap.height() > 0 else 1
            
            if pix_ratio > rect_ratio: # Wide
                new_h = int(thumb_rect.width() / pix_ratio)
                y_off = (thumb_rect.height() - new_h) // 2
                target = QRect(thumb_rect.x(), thumb_rect.y() + y_off, thumb_rect.width(), new_h)
            else: # Tall
                new_w = int(thumb_rect.height() * pix_ratio)
                x_off = (thumb_rect.width() - new_w) // 2
                target = QRect(thumb_rect.x() + x_off, thumb_rect.y(), new_w, thumb_rect.height())

            # Clip to rounded rect for thumbnail
            painter.save()
            clip_path = QPainterPath()
            clip_path.addRoundedRect(thumb_rect.x(), thumb_rect.y(), 
                                     thumb_rect.width(), thumb_rect.height(), 4, 4)
            painter.setClipPath(clip_path)
            painter.drawPixmap(target, pixmap)
            painter.restore()
        else:
            # Placeholder Logic
            painter.setBrush(QColor("#1D1D22"))
            painter.setPen(QColor("#2C2C34"))
            painter.drawRoundedRect(thumb_rect, 4, 4)
            
            # Check Status
            asset = index.data(Qt.ItemDataRole.UserRole)
            status_text = "No Preview"
            status_color = QColor("#87857F")
            
            if asset:
                status = asset.get('status', 'ready')
                if status == 'ingesting':
                    status_text = "Processing..."
                    status_color = QColor("#3EA8BF")
                elif status == 'corrupt':
                    status_text = "Error"
                    status_color = QColor("#D9635F")
                elif status == 'pending':
                    status_text = "Pending"
                    status_color = QColor("#D9A441")
            
            painter.setPen(status_color)
            painter.drawText(thumb_rect, Qt.AlignmentFlag.AlignCenter, status_text)

        # Text Area (below thumbnail, inside card)
        text_rect = QRect(card_rect.x() + self.padding, 
                         card_rect.y() + self.padding + self.thumb_height + 2, 
                         card_rect.width() - self.padding * 2, self.text_height)
        
        text_color = QColor("#E8E6E1") if is_selected else (QColor("#E8E6E1") if is_hovered else QColor("#E8E6E1"))
        painter.setPen(text_color)
        
        # Elide Text
        metrics = option.fontMetrics
        elided_text = metrics.elidedText(name, Qt.ElideMiddle, text_rect.width())
        painter.drawText(text_rect, Qt.AlignmentFlag.AlignCenter, elided_text)
        painter.restore()

class StockListDelegate(QStyledItemDelegate):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.height = 42
        self.icon_size = 36
        self.padding = 6

    def sizeHint(self, option, index):
        return QSize(option.rect.width(), self.height)

    def paint(self, painter, option, index):
        if not index.isValid(): return

        name = index.data(Qt.ItemDataRole.DisplayRole)
        pixmap = index.data(Qt.ItemDataRole.DecorationRole)
        asset_data = index.data(Qt.ItemDataRole.UserRole)
        is_selected = option.state & QStyle.StateFlag.State_Selected
        rect = option.rect
        
        painter.save()

        # Background
        if is_selected:
            painter.fillRect(rect, QColor("#3EA8BF"))
        elif option.state & QStyle.StateFlag.State_MouseOver:
            painter.fillRect(rect, QColor("#1D1D22"))

        # Text Color
        text_color = QColor("white") if is_selected else QColor("#E8E6E1")
        sub_text_color = QColor("#E8E6E1") if is_selected else QColor("#B4B1AA")

        # Icon Area
        icon_rect = QRect(rect.left() + self.padding, rect.top() + 3, self.icon_size, self.icon_size)
        
        if pixmap:
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
            # OPTIMIZATION: Draw directly into rect, let Qt handle scaling. 
            # Pre-scaled pixmap (360px) is small enough to be fast.
            painter.drawPixmap(icon_rect, pixmap)
        else:
            # Placeholder Logic with Status
            asset = index.data(Qt.ItemDataRole.UserRole)
            status = asset.get('status', 'ready') if asset else 'ready'
            
            bg_color = QColor("#26262D")
            txt = "-"
            
            if status == 'ingesting':
                 bg_color = QColor("#16323A") # Dark Blue
                 txt = "..."
            elif status == 'corrupt':
                 bg_color = QColor("#3A1F1E") # Dark Red
                 txt = "!"
            elif status == 'pending':
                 bg_color = QColor("#3A2F19") # Dark Orange
                 txt = "?"
                 
            painter.setBrush(bg_color)
            painter.setPen(Qt.NoPen)
            painter.drawRoundedRect(icon_rect, 4, 4)
            
            painter.setPen(QColor("#E8E6E1"))
            painter.drawText(icon_rect, Qt.AlignmentFlag.AlignCenter, txt)

        # Text Area (Name)
        name_rect = QRect(
            icon_rect.right() + 10, 
            rect.top(), 
            rect.width() - icon_rect.right() - 20, 
            self.height
        )
        
        font = painter.font()
        font.setPointSize(10); font.setBold(True)
        painter.setFont(font); painter.setPen(text_color)
        painter.drawText(name_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, name)
        
        # Details
        if asset_data:
            details = f"{asset_data.get('category','')} | {asset_data.get('file_type','')}"
            painter.setPen(sub_text_color)
            font.setBold(False); font.setPointSize(9)
            painter.setFont(font)
            painter.drawText(name_rect, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, details)

        painter.restore()
