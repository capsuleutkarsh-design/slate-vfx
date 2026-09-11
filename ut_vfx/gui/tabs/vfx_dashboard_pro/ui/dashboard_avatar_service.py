import os
import logging
import weakref
from datetime import datetime
from pathlib import Path
from typing import Optional, Tuple

from PySide6.QtCore import Qt, QObject
from PySide6.QtGui import QBrush, QPainter, QPixmap
from PySide6.QtWidgets import QFileDialog

from ut_vfx.core.domain.workers.file_io import FileOperationWorker
from ut_vfx.core.infra.database_manager import database_manager
from ut_vfx.core.infra.global_config import GlobalConfig

try:
    import shiboken6
except Exception:
    shiboken6 = None

logger = logging.getLogger(__name__)


class DashboardAvatarService:
    """Avatar upload/render helpers extracted from DashboardWidget."""

    @staticmethod
    def _is_qobject_alive(obj) -> bool:
        if obj is None:
            return False
        if shiboken6 is None:
            return True
        try:
            return shiboken6.isValid(obj)
        except Exception:
            return True

    @staticmethod
    def _safe_finished_callback(on_finished, username: str):
        owner = getattr(on_finished, "__self__", None)
        owner_ref = weakref.ref(owner) if isinstance(owner, QObject) else None

        def _invoke(success: bool, path: str):
            target = owner_ref() if owner_ref else None
            if target is not None and not DashboardAvatarService._is_qobject_alive(target):
                return
            try:
                on_finished(success, path, username)
            except RuntimeError as exc:
                logger.debug("Skipping stale avatar callback: %s", exc)

        return _invoke

    @staticmethod
    def choose_avatar_file(parent) -> str:
        path, _ = QFileDialog.getOpenFileName(
            parent,
            "Select Profile Picture",
            "",
            "Images (*.png *.jpg *.jpeg)",
        )
        return path or ""

    @staticmethod
    def build_avatar_destination(src_path: str, username: str) -> Path:
        server_root = GlobalConfig.server_root()
        avatar_dir = server_root / "Avatars"
        avatar_dir.mkdir(parents=True, exist_ok=True)
        ext = os.path.splitext(src_path)[1]
        ts = int(datetime.now().timestamp())
        return avatar_dir / f"{username}_{ts}{ext}"

    @staticmethod
    def start_avatar_upload(src_path: str, username: str, on_finished) -> Optional[FileOperationWorker]:
        if not src_path:
            return None
        dest_path = DashboardAvatarService.build_avatar_destination(src_path, username)
        worker = FileOperationWorker("copy", src_path, str(dest_path))
        worker.finished_op.connect(DashboardAvatarService._safe_finished_callback(on_finished, username))
        worker.finished.connect(worker.deleteLater)
        worker.start()
        return worker

    @staticmethod
    def finalize_avatar_upload(success: bool, result_path: str, username: str) -> Tuple[bool, str]:
        """
        Return (ok, message_or_path).
        If upload and DB update succeed, message_or_path is avatar path.
        """
        if not success:
            return False, f"Could not copy image: {result_path}"
        db_success = database_manager.update_user_profile_pic(username, result_path)
        if not db_success:
            return False, "Image uploaded but database update failed."
        return True, result_path

    @staticmethod
    def get_user_avatar_path(username: str) -> str:
        return database_manager.get_user_profile_pic(username) or ""

    @staticmethod
    def apply_avatar_to_label(label, path: str, size: int = 40) -> bool:
        if not label or not path:
            return False
        pixmap = QPixmap(path)
        if pixmap.isNull():
            return False

        rounded = QPixmap(size, size)
        rounded.fill(Qt.transparent)
        painter = QPainter(rounded)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setBrush(
            QBrush(
                pixmap.scaled(
                    size,
                    size,
                    Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                    Qt.TransformationMode.SmoothTransformation,
                )
            )
        )
        painter.setPen(Qt.NoPen)
        painter.drawEllipse(0, 0, size, size)
        painter.end()

        label.setPixmap(rounded)
        label.setText("")
        return True
