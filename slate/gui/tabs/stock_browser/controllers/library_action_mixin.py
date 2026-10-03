"""
Library actions of the Stock Viewer: delete (with Undo), clear, import,
export, favourites, studio picks and tags.

Every one of them says what happened, through the shared toast - deletion
with an Undo, export with Open folder - and never with a raw exception
(MED-017, MED-027, MED-034, MED-035, MED-041, MED-076).
"""

import json
import logging
from datetime import date
from pathlib import Path

from PySide6.QtCore import Qt, QStandardPaths, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QFileDialog

from .....core.infra.stock_repository import REMOVED

MANAGERS = "Only leads, supervisors and admins can"


def import_sentence(summary: dict) -> tuple:
    """(message, level): new, already there, not on disk and unreadable, each said apart (MED2-021)."""
    from .....core.domain.olive_lineup import plural
    imported = int(summary.get("imported") or 0)
    existing = int(summary.get("existing") or 0)
    missing = int(summary.get("missing") or 0)
    invalid = int(summary.get("invalid") or 0)
    parts = [f"Imported {plural(imported, 'new asset')}"]
    if existing:
        parts.append(f"{existing:,} {'was' if existing == 1 else 'were'} already in the library")
    if missing:
        parts.append(f"{plural(missing, 'file')} not on disk, left out")
    if invalid:
        parts.append(f"{plural(invalid, 'entry', 'entries')} could not be read")
    return "; ".join(parts) + ".", ("warning" if missing or invalid else "success")


def default_export_path() -> str:
    """Documents/Slate_Stock_Export_2026-10-02.json - not the program's working folder."""
    documents = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DocumentsLocation)
    folder = Path(documents) if documents else Path.home()
    return str(folder / f"Slate_Stock_Export_{date.today().isoformat()}.json")


def exportable(asset: dict) -> dict:
    """An asset as written to an export: only what means something on another server (MED2-022)."""
    from .....core.domain.asset_ingestor import ImportLibWorker
    return {k: v for k, v in asset.items() if k in ImportLibWorker.PORTABLE_KEYS}


class LibraryActionMixin:
    """
    Mixin for StockBrowserTab handling library management operations:
    Deletion, Clearing, Importing, and Exporting logic.
    Assumes self.can_ingest, self.lib_manager, self.model, self.proxy_model,
    and self.gallery are available.
    """

    def _get_selected_assets(self):
        """The selected assets, once each, in list order."""
        assets, seen = [], set()
        for asset in self.gallery.selected_assets():
            key = str(asset.get("id") or asset.get("file_path") or asset.get("path") or "")
            if key and key not in seen:
                seen.add(key)
                assets.append(asset)
        return assets

    # ------------------------------------------------------------- delete
    def _confirm_delete(self, count: int, names) -> bool:
        from ....components.feedback import confirm
        noun = "asset" if count == 1 else "assets"
        first = f"“{names[0]}”" if count == 1 else f"{count} {noun}"
        return confirm(
            self, "Delete from the stock library",
            f"Remove {first} from the stock library?",
            yes_label=f"Delete {count} {noun}" if count > 1 else "Delete",
            destructive=True,
            informative=("The source files are not touched, and nothing is removed from disk.\n"
                         "Undo it from the message that appears next, or later from Removed "
                         "in the sidebar. A Rescan leaves deleted assets out."))

    def delete_selected_assets(self):
        """Remove the selected assets from the library, with an Undo."""
        if not self.can_ingest:
            self._notify(f"{MANAGERS} delete stock.", "warning")
            return
        if getattr(self, "current_category", "") == REMOVED:
            # In Removed the same button brings them back (MED2-028).
            return self.restore_selected_assets()
        selected_assets = self._get_selected_assets()
        if not selected_assets:
            self._notify("Select one or more assets to delete.", "info")
            return
        count = len(selected_assets)
        names = [a.get("name") or a.get("file_name") or "" for a in selected_assets]
        if not self._confirm_delete(count, names):
            return

        try:
            deleted_ids = set(self.lib_manager.delete_assets(selected_assets))
        except Exception as e:
            logging.exception("Delete failed: %s", e)
            self._notify("The assets could not be deleted.", "error", details=str(e))
            return

        # Exactly the ones that went, by id - same-named files in other folders
        # are not confused with them (MED-076).
        gone = [a for a in selected_assets if str(a.get("id")).isdigit()
                and int(a.get("id")) in deleted_ids]
        failed = count - len(gone)
        if gone:
            self.model.remove_assets(gone)
            self.db_total = max(0, int(getattr(self, "db_total", 0) or 0) - len(gone))
            self.update_ui_counts()
            self._refresh_categories()
        if self.inspector.current_asset and any(
                str(a.get("id")) == str(self.inspector.current_asset.get("id")) for a in gone):
            self.inspector.clear()

        ids = sorted(deleted_ids)
        noun = "asset" if len(gone) == 1 else "assets"
        if failed and gone:
            self._notify(f"Deleted {len(gone)} {noun}; {failed} could not be deleted.", "warning",
                         action=("Undo", lambda: self.undo_delete(ids)))
        elif failed:
            self._notify("Nothing was deleted - the database refused the change.", "error")
        else:
            self._notify(f"Deleted {len(gone)} {noun} from the library.", "success",
                         action=("Undo", lambda: self.undo_delete(ids)))

    def undo_delete(self, ids):
        from .....core.domain.olive_lineup import plural
        restored = self.lib_manager.restore_assets(ids)
        if restored:
            self._notify(f"Restored {plural(restored, 'asset')}.", "success")
            self.load_library_from_server()
        else:
            self._notify("They could not be restored.", "error")

    def restore_selected_assets(self):
        """Removed: put the selected assets back in the library (MED2-028)."""
        if not self.can_ingest:
            return
        ids = [a.get("id") for a in self._get_selected_assets() if str(a.get("id", "")).isdigit()]
        if not ids:
            self._notify("Select one or more assets to restore.", "info")
            return
        self.undo_delete(ids)

    # -------------------------------------------------------------- clear
    def clear_entire_library(self):
        if not self.can_ingest:
            return
        from ..ui.dialogs import ClearLibraryDialog
        count = self.lib_manager.get_total_count()
        dialog = ClearLibraryDialog(count, self)
        if not dialog.exec() or not dialog.confirmed():
            return
        # Let go of every picture Slate holds from the cache first, or Windows
        # refuses to delete it (NEW-media-7).
        self.inspector.clear()
        self.model.clear()
        pool = getattr(self, "_stock_pool", None)
        if pool is not None:
            pool.waitForDone(5000)
        result = self.lib_manager.clear_all_assets()
        ok, removed, failed = result if isinstance(result, tuple) else (bool(result), 0, 0)
        if not ok:
            self._notify("The library could not be cleared.", "error")
            # The gallery was emptied to let go of the pictures: show what is
            # still there, or it looks cleared (MED2-027).
            self.load_library_from_server()
            return
        from .....core.domain.olive_lineup import plural
        self.model.clear_assets()
        self.db_total = 0
        self.inspector.clear()
        self._refresh_categories()
        self.update_ui_counts()
        extra = f" {plural(failed, 'cached file')} could not be removed." if failed else ""
        self._notify(f"Cleared the stock library ({plural(count, 'asset')}).{extra}",
                     "warning" if failed else "success")

    # ------------------------------------------------------------- import
    def import_library_file(self):
        if not self.can_ingest:
            return
        if getattr(self, "_import_worker", None) is not None and self._import_worker.isRunning():
            self._notify("An import is already running.", "info")
            return
        path, _ = QFileDialog.getOpenFileName(self, "Import a library export", "",
                                              "Slate library export (*.json)")
        if not path:
            return
        try:
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
        except (OSError, ValueError) as e:
            self._notify("That file could not be read as a library export.", "error", details=str(e))
            return
        from .....core.domain.asset_ingestor import ImportLibWorker
        entries, error = ImportLibWorker.validate(data)
        if error:
            self._notify(error, "error")
            return
        worker = ImportLibWorker(entries, username=getattr(self, "username", ""))
        worker.progress_signal.connect(lambda pct, text: self.sidebar.set_ingest_progress(pct, text))
        worker.summary_ready.connect(self._on_import_summary)
        worker.finished.connect(worker.deleteLater)
        self._import_worker = worker
        self.sidebar.set_ingest_state("Importing…", True)
        self.sidebar.set_ingest_running(True)
        worker.start()

    def _on_import_summary(self, summary):
        self._import_worker = None
        self.sidebar.set_ingest_running(False)
        if summary.get("error"):
            self.sidebar.set_ingest_state(summary["error"], True)
            self._notify(summary["error"], "error")
            return
        text, level = import_sentence(summary)
        self.sidebar.set_ingest_state(text, True)
        self._notify(text, level)
        self.load_library_from_server()

    # ------------------------------------------------------------- export
    def export_library(self):
        if not self.can_ingest:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Export the library", default_export_path(),
                                              "Slate library export (*.json)")
        if not path:
            return
        try:
            self.lib_manager.load_library()
            data = [exportable(a) for a in self.lib_manager.get_all_assets()]
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, default=str, ensure_ascii=False)
        except Exception as e:
            self._notify("The library could not be exported.", "error", details=str(e))
            return
        from .....core.domain.olive_lineup import plural
        folder = str(Path(path).parent)
        self._notify(f"Exported {plural(len(data), 'asset')} to {Path(path).name}.", "success",
                     action=("Open folder", lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(folder))))

    # ------------------------------------------------ favourites, picks, tags
    def toggle_favorite(self, assets=None, on=None):
        """Star or unstar: one person's favourites (MED-009)."""
        assets = assets if assets is not None else self._get_selected_assets()
        assets = [a for a in assets if str(a.get("id", "")).isdigit()]
        if not assets:
            return
        if on is None:
            on = not all(a.get("is_favorite") for a in assets)
        done = 0
        for asset in assets:
            if self.lib_manager.set_favorite(asset["id"], on):
                asset["is_favorite"] = bool(on)
                self.model.update_item({"id": asset["id"], "file_path": asset.get("file_path"),
                                        "is_favorite": bool(on)})
                done += 1
        if done != len(assets):
            self._notify("Your favourites could not be saved.", "error")
        if self.inspector.current_asset and any(
                str(a["id"]) == str(self.inspector.current_asset.get("id")) for a in assets):
            self.inspector.set_favorite(bool(on))
        self.proxy_model.invalidateFilter()
        self._refresh_categories()
        self.update_ui_counts()

    def toggle_pick(self, assets=None, on=None):
        """Mark or unmark studio picks - for people who manage the library."""
        if not self.can_ingest:
            self._notify(f"{MANAGERS} choose studio picks.", "warning")
            return
        assets = assets if assets is not None else self._get_selected_assets()
        assets = [a for a in assets if str(a.get("id", "")).isdigit()]
        if not assets:
            return
        if on is None:
            on = not all(a.get("is_pick") for a in assets)
        for asset in assets:
            if self.lib_manager.set_pick(asset["id"], on):
                asset["is_pick"] = bool(on)
                self.model.update_item({"id": asset["id"], "file_path": asset.get("file_path"),
                                        "is_pick": bool(on)})
        if self.inspector.current_asset and any(
                str(a["id"]) == str(self.inspector.current_asset.get("id")) for a in assets):
            self.inspector.set_pick(bool(on))
        self.proxy_model.invalidateFilter()
        self._refresh_categories()
        self.update_ui_counts()
        noun = "asset" if len(assets) == 1 else "assets"
        self._notify(f"{'Added' if on else 'Removed'} {len(assets)} {noun} "
                     f"{'to' if on else 'from'} the studio picks.", "success")

    def edit_tags_of(self, asset=None):
        """Edit one asset's tags (MED-031)."""
        if not self.can_ingest:
            self._notify(f"{MANAGERS} edit stock tags.", "warning")
            return
        if asset is None:
            selected = self._get_selected_assets()
            asset = selected[0] if selected else None
        if not asset or not str(asset.get("id", "")).isdigit():
            return
        from ....widgets.tag_edit_dialog import TagEditDialog
        from .....core.domain.stock_search import real_tags
        current = real_tags(asset.get("tags"))
        dialog = TagEditDialog(self, current_tags=current,
                               available_tags=self.lib_manager.get_all_tags())
        if not dialog.exec():
            return
        tags = dialog.get_tags()
        if tags == current:
            return
        if self.lib_manager.set_tags(asset, tags):
            asset["tags"] = list(tags)
            self.model.update_item({"id": asset["id"], "file_path": asset.get("file_path"),
                                    "tags": list(tags)})
            self.inspector.refresh_facts(asset)
            self._notify("Tags saved.", "success")
        else:
            self._notify("The tags could not be saved.", "error")
