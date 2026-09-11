import logging
from typing import Any, Dict, List, Optional

from .library_manager import LibraryManager
from ..infra.database_manager import database_manager

logger = logging.getLogger(__name__)


class AssetAPI:
    """
    Standardized asset API facade for UTCAP.

    This facade keeps legacy LibraryManager compatibility while exposing
    stable API names that can later map to OpenAssetIO providers.
    """

    def __init__(self, backend: Any):
        self._backend = backend

    # --- Standardized methods ---
    def list_assets(
        self,
        query: Optional[str] = None,
        limit: Optional[int] = None,
        offset: int = 0,
        file_types: Optional[List[str]] = None,
        asset_ids: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        return self._backend.search_library(
            query=query,
            limit=limit,
            offset=offset,
            file_types=file_types,
            asset_ids=asset_ids,
        )

    def count_assets(self) -> int:
        return int(self._backend.get_total_count() or 0)

    def create_assets_batch(self, assets: List[Dict[str, Any]]) -> None:
        self._backend.add_assets_batch(assets)

    def update_asset_record(self, asset_id: str, updated_data: Dict[str, Any]) -> None:
        self._backend.update_asset(asset_id, updated_data)

    def delete_asset_record(self, asset: Dict[str, Any]) -> bool:
        return bool(self._backend.delete_asset(asset))

    def clear_assets(self) -> bool:
        return bool(self._backend.clear_all_assets())

    # --- Legacy compatibility passthroughs ---
    def get_total_count(self):
        return self._backend.get_total_count()

    def search_library(self, *args, **kwargs):
        return self._backend.search_library(*args, **kwargs)

    def add_assets_batch(self, *args, **kwargs):
        return self._backend.add_assets_batch(*args, **kwargs)

    def update_asset(self, *args, **kwargs):
        return self._backend.update_asset(*args, **kwargs)

    def delete_asset(self, *args, **kwargs):
        return self._backend.delete_asset(*args, **kwargs)

    def clear_all_assets(self, *args, **kwargs):
        return self._backend.clear_all_assets(*args, **kwargs)

    def get_categories(self, *args, **kwargs):
        return self._backend.get_categories(*args, **kwargs)

    def get_all_assets(self, *args, **kwargs):
        return self._backend.get_all_assets(*args, **kwargs)

    def __getattr__(self, item):
        # Delegate any other legacy behavior transparently.
        return getattr(self._backend, item)


def _create_legacy_backend(db_manager=None, library_manager=None):
    if library_manager is not None:
        return library_manager
    return LibraryManager(db_manager or database_manager)


def _try_create_openassetio_backend(library_manager=None, db_manager=None):
    """
    Attempt to create an OpenAssetIO-backed adapter.

    If successful, returns an OpenAssetIOBackend that wraps LibraryManager
    behind OAIO concepts (resolve, register, trait sets) while maintaining
    full backward compatibility with all legacy LibraryManager methods.

    Returns None if openassetio is not installed or backend creation fails.
    """
    try:
        from .openassetio_backend import try_create_openassetio_backend

        # Ensure we have a LibraryManager to wrap
        lib_manager = library_manager or LibraryManager(db_manager or database_manager)
        backend = try_create_openassetio_backend(lib_manager)

        if backend is not None:
            logger.info(f"AssetAPI: OpenAssetIO backend active (oaio={backend.has_oaio})")
            return backend

    except ImportError:
        logger.debug("AssetAPI: openassetio_backend module not available")
        # Silencing the warning because OpenAssetIO is optional and we fall back to our fast native DB seamlessly.
        logger.info("AssetAPI: OpenAssetIO is not installed. Using native DB backend.")
    except Exception as e:
        logger.debug(f"AssetAPI: OpenAssetIO backend creation failed: {e}")
        logger.info("AssetAPI: OpenAssetIO is not active. Using native DB backend.")

    return None


def create_asset_api(db_manager=None, library_manager=None, preferred_backend: Optional[str] = None) -> AssetAPI:
    """
    Factory function to create an AssetAPI with the best available backend.

    Args:
        db_manager: Optional database manager instance.
        library_manager: Optional pre-existing LibraryManager.
        preferred_backend: 'openassetio' to prefer OAIO, 'legacy' for direct
                          LibraryManager, or None for auto-detection.

    Auto-detection priority:
        1. If openassetio is installed → OpenAssetIOBackend (wraps LibraryManager)
        2. Otherwise → raw LibraryManager
    """
    backend_choice = str(preferred_backend or "auto").strip().lower()

    # Explicit OpenAssetIO request
    if backend_choice in {"openassetio", "open_asset_io", "oaio"}:
        openasset_backend = _try_create_openassetio_backend(
            library_manager=library_manager, db_manager=db_manager
        )
        if openasset_backend is not None:
            return AssetAPI(openasset_backend)
        logger.warning("AssetAPI: OpenAssetIO backend unavailable. Falling back to legacy backend.")

    # Auto-detection: try OpenAssetIO first if not explicitly requesting legacy
    if backend_choice == "auto":
        openasset_backend = _try_create_openassetio_backend(
            library_manager=library_manager, db_manager=db_manager
        )
        if openasset_backend is not None:
            return AssetAPI(openasset_backend)

    return AssetAPI(_create_legacy_backend(db_manager=db_manager, library_manager=library_manager))


