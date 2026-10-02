"""
Shot thumbnails, for the detail panel.

The grid has no thumbnail column, yet every filter queued a thumbnail for every
shot on screen. They are now loaded only for the shot whose panel is open.

What a thumbnail looks like on screen is not shot data. The loader used to
write its placeholder ('placeholder_red.png' in the install folder of the
machine that happened to look) into shot.thumbnail_path, and the next save
stored that path in the database and the Excel backup for everyone. The
picture shown now lives in shot._display_thumb, which is never saved.

Thumbnails are keyed by project, reel and shot: SH010 in two reels used to
share one picture.
"""

import os
from collections import deque

from PySide6.QtCore import QTimer
from PySide6.QtGui import QPixmap

from slate.core.infra.global_config import GlobalConfig


class DashboardThumbnailMixin:
    """Mixed into DashboardWidget."""

    def _init_thumbnail_system(self):
        self._thumb_requests_inflight = set()
        # Kept for older callers; nothing is prefetched any more.
        self._thumb_prefetch_queue = deque()
        self._thumb_prefetch_ids = set()
        self._thumb_prefetch_timer = QTimer(self)
        self._visible_thumb_timer = QTimer(self)

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

    def _thumb_identifier(self, shot) -> str:
        code = self.current_project.code if self.current_project else "UNKNOWN"
        reel = getattr(shot, "reel_episode", "") or ""
        return f"{code}|{reel}|{getattr(shot, 'shot_name', shot)}"

    @staticmethod
    def _parse_identifier(identifier: str):
        parts = str(identifier).split("|", 2)
        if len(parts) == 3:
            return parts[0], parts[1], parts[2]
        return "", "", str(identifier)

    @classmethod
    def _shot_name_from_identifier(cls, identifier: str) -> str:
        return cls._parse_identifier(identifier)[2]

    @classmethod
    def _project_code_from_identifier(cls, identifier: str) -> str:
        return cls._parse_identifier(identifier)[0]

    def _shot_for_identifier(self, identifier):
        _code, reel, name = self._parse_identifier(identifier)
        for shot in self.all_shots or []:
            if shot.shot_name == name and (shot.reel_episode or "") == reel:
                return shot
        return None

    def _needs_thumbnail_refresh(self, shot) -> bool:
        if not shot or not shot.shot_name:
            return False
        if self._thumb_identifier(shot) in self.image_cache:
            return False
        thumb_path = self._resolve_thumb_path(getattr(shot, "thumbnail_path", ""))
        if not thumb_path or self._is_placeholder_thumb(thumb_path) or not os.path.exists(thumb_path):
            return True
        try:
            return os.path.getsize(thumb_path) <= 0
        except OSError:
            return True

    def _queue_thumbnail_load(self, shot):
        if not self.current_project or not shot or not shot.shot_name:
            return
        identifier = self._thumb_identifier(shot)
        if identifier in self._thumb_requests_inflight:
            return
        self._thumb_requests_inflight.add(identifier)
        self.image_loader.load_image(
            identifier, self.current_project.code, shot.reel_episode, shot.shot_name,
            self.current_project.folder_base)

    def _load_detail_thumbnail(self, shot):
        """Show the open shot's picture: cached, stored, or fetched in the background."""
        detail = getattr(self, "detail_widget", None)
        if detail is None:
            return
        cached = self.image_cache.get(self._thumb_identifier(shot))
        if cached is not None:
            detail.set_thumbnail(cached)
            return
        stored = self._resolve_thumb_path(getattr(shot, "thumbnail_path", ""))
        if stored and not self._is_placeholder_thumb(stored) and os.path.exists(stored):
            pixmap = QPixmap(stored)
            if not pixmap.isNull():
                detail.set_thumbnail(pixmap)
                return
        detail.set_thumbnail(None, "Loading preview…")
        self._queue_thumbnail_load(shot)

    def on_image_started(self, identifier):
        """The loader started on a thumbnail. Nothing is written to the shot."""
        return

    def on_image_loaded(self, identifier, path, image):
        self._thumb_requests_inflight.discard(identifier)
        if self.current_project and self._project_code_from_identifier(identifier) not in (
                "", self.current_project.code):
            return
        shot = self._shot_for_identifier(identifier)
        pixmap = QPixmap.fromImage(image) if image is not None else QPixmap()
        placeholder = self._is_placeholder_thumb(path) or pixmap.isNull()
        if not placeholder:
            while len(self.image_cache) >= 150:
                try:
                    self.image_cache.popitem(last=False)
                except Exception:
                    break
            self.image_cache[identifier] = pixmap
            if shot is not None:
                shot._display_thumb = path
        detail = getattr(self, "detail_widget", None)
        if detail is not None and shot is not None and getattr(detail, "shot", None) is shot:
            if placeholder:
                detail.set_thumbnail(None, "No preview yet")
            else:
                detail.set_thumbnail(pixmap)

    def _cancel_thumbnail_prefetch(self):
        self._thumb_prefetch_queue.clear()
        self._thumb_prefetch_ids.clear()

    def _schedule_visible_thumbnail_refresh(self, *_args):
        return

    def _queue_visible_thumbnails(self):
        return

    def start_thumbnail_loading(self):
        """Nothing to preload: the grid shows no thumbnails (kept for older callers)."""
        return
