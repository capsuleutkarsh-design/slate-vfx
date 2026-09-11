"""
OpenAssetIO Backend — Adapter for UTCAP Asset Management

Wraps the existing LibraryManager behind the OpenAssetIO ManagerInterface,
enabling UTCAP to participate in standardised VFX asset resolution workflows.

Architecture:
    ┌──────────────┐      ┌──────────────────────┐      ┌─────────────────┐
    │   AssetAPI    │ ───▶ │ OpenAssetIOBackend    │ ───▶ │ LibraryManager  │
    │   (facade)   │      │ (this adapter)        │      │ (PostgreSQL)    │
    └──────────────┘      └──────────────────────┘      └─────────────────┘

If OpenAssetIO is not installed, the system gracefully falls back to the
legacy LibraryManager via the existing AssetAPI facade.

This module does NOT import openassetio at module level so that it can
be imported safely even when the library is not installed.
"""

import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


class OpenAssetIOBackend:
    """
    Bridges the OpenAssetIO ManagerInterface to the UTCAP LibraryManager.

    Exposes the same method signatures as LibraryManager so that AssetAPI
    can transparently switch between legacy and OpenAssetIO backends.

    OpenAssetIO concepts mapped:
        Entity Reference ↔ asset file_path (the canonical identifier)
        Trait Set        ↔ asset metadata dict (tags, resolution, codec)
        Manager          ↔ this class (wrapping LibraryManager)
    """

    # Identifier string for the OAIO plugin system
    IDENTIFIER = "org.utcap.manager"

    def __init__(self, library_manager, manager_interface=None):
        """
        Args:
            library_manager: The existing LibraryManager instance (DB-backed).
            manager_interface: Optional real openassetio.ManagerInterface
                if one was successfully created from an OAIO plugin.
                When provided, resolve/register calls delegate to it.
        """
        self._lib = library_manager
        self._oaio = manager_interface  # May be None
        self._initialized = False
        self._try_initialize()

    def _try_initialize(self):
        """Attempt to initialize the OAIO manager interface."""
        if self._oaio is not None:
            try:
                # ManagerInterface.initialize() is idempotent
                if hasattr(self._oaio, 'initialize') and not self._initialized:
                    # Pass minimal settings — UTCAP doesn't use external OAIO hosts yet
                    self._oaio.initialize({})
                    self._initialized = True
                    logger.info("OpenAssetIO ManagerInterface initialized")
            except Exception as e:
                logger.warning(f"OpenAssetIO manager init failed: {e}")
                self._oaio = None

    # ------------------------------------------------------------------
    # Asset resolution (OpenAssetIO-style)
    # ------------------------------------------------------------------

    def resolve(self, entity_reference: str) -> Optional[Dict[str, Any]]:
        """
        Resolve an entity reference to asset data.

        In UTCAP, an entity reference is the asset's file_path.
        Returns the legacy asset dict, or None if not found.
        """
        if self._oaio and hasattr(self._oaio, 'resolve'):
            try:
                result = self._oaio.resolve(entity_reference)
                if result:
                    return result
            except Exception as e:
                logger.debug(f"OAIO resolve failed, falling back: {e}")

        # Fallback: search local DB via LibraryManager
        results = self._lib.search_library(query=entity_reference, limit=1)
        return results[0] if results else None

    def register(self, entity_reference: str, trait_data: Dict[str, Any]) -> Optional[str]:
        """
        Register (publish) an asset.

        Maps the OAIO register concept to LibraryManager.add_asset().
        Returns the new asset ID on success.
        """
        if self._oaio and hasattr(self._oaio, 'register'):
            try:
                result = self._oaio.register(entity_reference, trait_data)
                if result:
                    return result
            except Exception as e:
                logger.debug(f"OAIO register failed, falling back: {e}")

        # Fallback: add to local DB
        name = trait_data.get('name', entity_reference.split('/')[-1])
        category = trait_data.get('category', 'Uncategorized')
        tags = trait_data.get('tags', [])
        metadata = trait_data.get('metadata', {})
        thumb_path = trait_data.get('thumb_path')
        proxy_path = trait_data.get('proxy_path')

        self._lib.add_asset(
            name=name,
            path=entity_reference,
            category=category,
            tags=tags,
            metadata=metadata,
            thumb_path=thumb_path,
            proxy_path=proxy_path,
        )
        return entity_reference

    def is_entity_reference(self, token: str) -> bool:
        """Check if a string looks like an entity reference we can resolve."""
        if self._oaio and hasattr(self._oaio, 'isEntityReferenceString'):
            try:
                return self._oaio.isEntityReferenceString(token)
            except Exception:
                pass
        # Heuristic: any absolute path or UTCAP-style reference
        return token.startswith(('/', '\\\\', 'utcap://')) or ':' in token

    # ------------------------------------------------------------------
    # Legacy LibraryManager compatibility (pass-through)
    # ------------------------------------------------------------------

    def search_library(self, *args, **kwargs):
        return self._lib.search_library(*args, **kwargs)

    def get_total_count(self):
        return self._lib.get_total_count()

    def add_assets_batch(self, *args, **kwargs):
        return self._lib.add_assets_batch(*args, **kwargs)

    def update_asset(self, *args, **kwargs):
        return self._lib.update_asset(*args, **kwargs)

    def delete_asset(self, *args, **kwargs):
        return self._lib.delete_asset(*args, **kwargs)

    def clear_all_assets(self, *args, **kwargs):
        return self._lib.clear_all_assets(*args, **kwargs)

    def get_categories(self, *args, **kwargs):
        return self._lib.get_categories(*args, **kwargs)

    def get_all_assets(self, *args, **kwargs):
        return self._lib.get_all_assets(*args, **kwargs)

    def get_all_tags(self, *args, **kwargs):
        return self._lib.get_all_tags(*args, **kwargs)

    def load_library(self, *args, **kwargs):
        return self._lib.load_library(*args, **kwargs)

    def update_asset_metadata(self, *args, **kwargs):
        return self._lib.update_asset_metadata(*args, **kwargs)

    def toggle_favorite_status(self, *args, **kwargs):
        return self._lib.toggle_favorite_status(*args, **kwargs)

    def trash_asset(self, *args, **kwargs):
        return self._lib.trash_asset(*args, **kwargs)

    # Transparent delegation for any methods not explicitly listed
    def __getattr__(self, item):
        return getattr(self._lib, item)

    # ------------------------------------------------------------------
    # Status / Introspection
    # ------------------------------------------------------------------

    @property
    def has_oaio(self) -> bool:
        """True if a real OpenAssetIO manager is active."""
        return self._oaio is not None and self._initialized

    def get_info(self) -> Dict[str, Any]:
        """Return backend info for diagnostics / settings UI."""
        info = {
            "backend": "OpenAssetIOBackend",
            "identifier": self.IDENTIFIER,
            "oaio_active": self.has_oaio,
            "library_manager": type(self._lib).__name__,
        }
        if self._oaio and hasattr(self._oaio, 'identifier'):
            info["oaio_identifier"] = self._oaio.identifier()
        return info


def try_create_openassetio_backend(library_manager) -> Optional[OpenAssetIOBackend]:
    """
    Attempt to create an OpenAssetIO-backed adapter.

    Returns OpenAssetIOBackend on success, None if openassetio is
    not installed or no manager can be created.
    """
    try:
        import openassetio  # noqa: F401
        from openassetio.hostApi import HostInterface, ManagerFactory
        from openassetio.pluginSystem import PythonPluginSystemManagerImplementationFactory
        from openassetio.log import ConsoleLogger

        logger.info(f"OpenAssetIO {getattr(openassetio, '__version__', 'unknown')} detected")

        # Create a minimal host interface for UTCAP
        class UTCAPHostInterface(HostInterface):
            def identifier(self):
                return "org.utcap.host"

            def displayName(self):
                return "UTCAP VFX Pipeline"

        host_interface = UTCAPHostInterface()
        oaio_logger = ConsoleLogger()

        # Try to discover and create a manager from environment / config
        try:
            impl_factory = PythonPluginSystemManagerImplementationFactory(oaio_logger)
            factory = ManagerFactory(host_interface, impl_factory, oaio_logger)
            available = factory.availableManagers()

            if available:
                # Use first available manager
                manager_id = list(available.keys())[0]
                logger.info(f"OpenAssetIO: Using manager '{manager_id}'")
                manager = factory.createManager(manager_id)
                return OpenAssetIOBackend(library_manager, manager_interface=manager)
            else:
                logger.info("OpenAssetIO: No external managers found, using UTCAP adapter only")
                return OpenAssetIOBackend(library_manager, manager_interface=None)

        except Exception as e:
            logger.info(f"OpenAssetIO: Manager creation skipped ({e}), using UTCAP adapter")
            return OpenAssetIOBackend(library_manager, manager_interface=None)

    except ImportError:
        logger.debug("OpenAssetIO not installed — backend unavailable")
        return None
    except Exception as e:
        logger.warning(f"OpenAssetIO backend creation failed: {e}")
        return None
