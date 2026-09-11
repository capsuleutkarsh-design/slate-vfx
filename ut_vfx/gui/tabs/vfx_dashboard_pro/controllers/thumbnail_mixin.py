import os
from collections import deque
from PySide6.QtCore import QTimer
from ut_vfx.core.infra.global_config import GlobalConfig

class DashboardThumbnailMixin:
    """
    Mixin class to handle Thumbnail Pre-fetching, Loading, and Caching 
    for the VFX Dashboard.
    Requires the host class to have:
      - self.image_cache
      - self._thumb_requests_inflight
      - self.current_project
      - self.image_loader
      - self.table (optional)
      - self.displayed_shots
    """
    
    def _init_thumbnail_system(self):
        self._thumb_requests_inflight = set()
        self._thumb_prefetch_queue = deque()
        self._thumb_prefetch_ids = set()
        self._thumb_prefetch_timer = QTimer(self)
        self._thumb_prefetch_timer.setInterval(120)
        self._thumb_prefetch_timer.timeout.connect(self._process_thumbnail_prefetch_batch)
        self._visible_thumb_timer = QTimer(self)
        self._visible_thumb_timer.setSingleShot(True)
        self._visible_thumb_timer.setInterval(120)
        self._visible_thumb_timer.timeout.connect(self._queue_visible_thumbnails)

    @staticmethod
    def _is_placeholder_thumb(path: str) -> bool:
        path_text = str(path or "").lower()
        return "placeholder_yellow" in path_text or "placeholder_red" in path_text

    @staticmethod
    def _resolve_thumb_path(path: str) -> str:
        raw = str(path or "").strip()
        if not raw:
            return ""
        if "$SERVER" in raw:
            return GlobalConfig.resolve_path(raw)
        return raw

    def _thumb_identifier(self, shot_name: str) -> str:
        code = self.current_project.code if self.current_project else "UNKNOWN"
        return f"{code}:{shot_name}"

    @staticmethod
    def _shot_name_from_identifier(identifier: str) -> str:
        if ":" not in str(identifier):
            return str(identifier)
        return str(identifier).split(":", 1)[1]

    @staticmethod
    def _project_code_from_identifier(identifier: str) -> str:
        if ":" not in str(identifier):
            return ""
        return str(identifier).split(":", 1)[0]

    def _needs_thumbnail_refresh(self, shot) -> bool:
        if not shot or not shot.shot_name:
            return False

        identifier = self._thumb_identifier(shot.shot_name)
        if identifier in self.image_cache:
            return False

        thumb_path = str(getattr(shot, "thumbnail_path", "") or "").strip()
        if not thumb_path:
            return True
        if self._is_placeholder_thumb(thumb_path):
            return True

        resolved = self._resolve_thumb_path(thumb_path)
        if not resolved or not os.path.exists(resolved):
            return True

        try:
            return os.path.getsize(resolved) <= 0
        except OSError:
            return True

    def _queue_thumbnail_load(self, shot):
        if not self.current_project or not shot or not shot.shot_name:
            return

        identifier = self._thumb_identifier(shot.shot_name)
        if identifier in self._thumb_requests_inflight:
            return

        self._thumb_requests_inflight.add(identifier)
        root_path = self.current_project.folder_base
        self.image_loader.load_image(
            identifier,
            self.current_project.code,
            shot.reel_episode,
            shot.shot_name,
            root_path,
        )

    def _cancel_thumbnail_prefetch(self):
        if getattr(self, '_thumb_prefetch_timer', None) and self._thumb_prefetch_timer.isActive():
            self._thumb_prefetch_timer.stop()
        if getattr(self, '_visible_thumb_timer', None) and self._visible_thumb_timer.isActive():
            self._visible_thumb_timer.stop()
        if getattr(self, '_thumb_prefetch_queue', None) is not None:
            self._thumb_prefetch_queue.clear()
        if getattr(self, '_thumb_prefetch_ids', None) is not None:
            self._thumb_prefetch_ids.clear()

    def _visible_shots(self, lookahead: int = 18):
        if not self.displayed_shots:
            return []
        if not hasattr(self, "table"):
            return self.displayed_shots[:lookahead]

        top = self.table.rowAt(0)
        bottom = self.table.rowAt(max(self.table.viewport().height() - 1, 0))
        if top < 0:
            top = 0
        if bottom < top:
            bottom = min(len(self.displayed_shots) - 1, top + lookahead - 1)
        else:
            bottom = min(len(self.displayed_shots) - 1, max(bottom, top + lookahead - 1))

        return self.displayed_shots[top:bottom + 1]

    def _queue_visible_thumbnails(self):
        if not self.current_project or not self.isVisible():
            return
        for shot in self._visible_shots():
            if self._needs_thumbnail_refresh(shot):
                self._queue_thumbnail_load(shot)

    def _enqueue_thumbnail_prefetch(self, shot):
        if not shot or not getattr(shot, "shot_name", None):
            return
        identifier = self._thumb_identifier(shot.shot_name)
        if identifier in getattr(self, '_thumb_prefetch_ids', set()):
            return
        if identifier in getattr(self, 'image_cache', {}):
            return
        if identifier in getattr(self, '_thumb_requests_inflight', set()):
            return
        self._thumb_prefetch_ids.add(identifier)
        self._thumb_prefetch_queue.append(shot)

    def _process_thumbnail_prefetch_batch(self):
        if not self.current_project or not self.isVisible():
            if self._thumb_prefetch_timer.isActive():
                self._thumb_prefetch_timer.stop()
            return
        if not self._thumb_prefetch_queue:
            self._thumb_prefetch_timer.stop()
            return

        max_threads = getattr(self.image_loader.thread_pool, "maxThreadCount", lambda: 2)()
        max_inflight = max(2, max_threads * 2)
        budget = max(1, max_inflight - len(self._thumb_requests_inflight))
        dispatched = 0
        while self._thumb_prefetch_queue and dispatched < budget:
            shot = self._thumb_prefetch_queue.popleft()
            identifier = self._thumb_identifier(getattr(shot, "shot_name", ""))
            self._thumb_prefetch_ids.discard(identifier)
            if self._needs_thumbnail_refresh(shot):
                self._queue_thumbnail_load(shot)
                dispatched += 1

        if not self._thumb_prefetch_queue:
            self._thumb_prefetch_timer.stop()

    def _schedule_visible_thumbnail_refresh(self, *_args):
        if not self.current_project or not self.isVisible():
            return
        if getattr(self, '_visible_thumb_timer', None):
            self._visible_thumb_timer.start()

    def start_thumbnail_loading(self):
        if not self.current_project:
            return
        self._cancel_thumbnail_prefetch()
        self._queue_visible_thumbnails()

        visible_names = {s.shot_name for s in self._visible_shots() if getattr(s, "shot_name", None)}
        for shot in getattr(self, "displayed_shots", []):
            shot_name = getattr(shot, "shot_name", "")
            if not shot_name or shot_name in visible_names:
                continue
            self._enqueue_thumbnail_prefetch(shot)

        if getattr(self, "_thumb_prefetch_queue", None) and self.isVisible():
            if getattr(self, "_thumb_prefetch_timer", None):
                self._thumb_prefetch_timer.start()
