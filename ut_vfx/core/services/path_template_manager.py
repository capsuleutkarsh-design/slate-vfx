"""
Path Template Management using Lucidity (VFX Industry Standard)

This service provides centralized path template management for studio pipelines
using the industry-standard lucidity library.

Benefits:
- Centralized path definitions (single source of truth)
- Automatic validation (prevents invalid paths)
- Bidirectional parsing (path -> metadata, metadata -> path)
- Industry-standard approach (used by major VFX studios)
"""

from typing import Dict, Any, Optional, List
import logging

try:
    import lucidity
    HAS_LUCIDITY = True
except ImportError:
    HAS_LUCIDITY = False
    logging.warning("lucidity not available - path templates disabled. Install with: pip install lucidity")


class PathTemplateManager:
    """
    Centralized path template system using Lucidity.

    Replaces manual f-string path construction with validated templates.
    Provides bidirectional path <-> metadata conversion.

    Usage:
        # Initialize
        mgr = PathTemplateManager()

        # Generate path from metadata
        path = mgr.format_path('render',
            project='MARVEL', sequence='sq010', shot='sh0020',
            task='comp', version=3, frame=1001
        )
        # Result: "X:/Projects/MARVEL/sq010/sh0020/comp/v003/render/sh0020_comp_v003.1001.exr"

        # Parse path to extract metadata
        metadata = mgr.parse_path('render', path)
        # Result: {'project': 'MARVEL', 'sequence': 'sq010', ...}
    """

    def __init__(self, root_path: Optional[str] = None):
        """
        Initialize path template manager.

        Args:
            root_path: Optional root path for all templates (default: X:/Projects)
        """
        import os

        self.root_path = root_path or os.environ.get("UTVFX_PROJECTS_ROOT", "X:/Projects")
        self.templates = {}
        self._init_templates()

    def is_available(self) -> bool:
        """Check if lucidity is available."""
        return HAS_LUCIDITY

    def _init_templates(self):
        """Initialize studio path templates."""
        if not HAS_LUCIDITY:
            logging.debug("Lucidity not available, templates will not work")
            return

        # Original templates
        self.templates['render'] = lucidity.Template(
            'render',
            '{root}/{project}/{sequence}/{shot}/{task}/v{version:03d}/render/{shot}_{task}_v{version:03d}.{frame:04d}.exr'
        )

        self.templates['source'] = lucidity.Template(
            'source',
            '{root}/{project}/source/{date}/{reel}/{reel}_{frame:06d}.{ext}'
        )

        self.templates['stock'] = lucidity.Template(
            'stock',
            '{root}/Stock/{category}/{asset_type}/{asset_name}.{ext}'
        )

        self.templates['publish'] = lucidity.Template(
            'publish',
            '{root}/{project}/{sequence}/{shot}/{task}/publish/{shot}_{task}_v{version:03d}_{variant}.{ext}'
        )

        self.templates['work'] = lucidity.Template(
            'work',
            '{root}/{project}/{sequence}/{shot}/{task}/work/{shot}_{task}_wip_v{version:03d}.{ext}'
        )

        self.templates['preview'] = lucidity.Template(
            'preview',
            '{root}/{project}/{sequence}/{shot}/{task}/preview/{shot}_{task}_v{version:03d}.{ext}'
        )

        # UT_VFX specific templates
        self.templates['project_base'] = lucidity.Template(
            'project_base',
            '{root}/{project}'
        )

        self.templates['reels_root'] = lucidity.Template(
            'reels_root',
            '{root}/{project}/05_Reels'
        )

        self.templates['reel_base'] = lucidity.Template(
            'reel_base',
            '{root}/{project}/05_Reels/{reel}'
        )

        self.templates['shot_base'] = lucidity.Template(
            'shot_base',
            '{root}/{project}/05_Reels/{reel}/{shot}'
        )

        self.templates['shot_scan'] = lucidity.Template(
            'shot_scan',
            '{root}/{project}/05_Reels/{reel}/{shot}/01_Scan'
        )

        self.templates['shot_comp'] = lucidity.Template(
            'shot_comp',
            '{root}/{project}/05_Reels/{reel}/{shot}/03_Comp'
        )

        self.templates['shot_output'] = lucidity.Template(
            'shot_output',
            '{root}/{project}/05_Reels/{reel}/{shot}/08_Output'
        )

        self.templates['stock_library'] = lucidity.Template(
            'stock_library',
            '{root}/Extra/UT_Central/Stock_Library/{category}'
        )

        self.templates['stock_cache'] = lucidity.Template(
            'stock_cache',
            '{root}/Extra/UT_Central/Stock_Cache'
        )

        logging.info(f"Initialized {len(self.templates)} path templates")

    def format_path(self, template_name: str, **kwargs: Any) -> str:
        """
        Generate path from template.

        Args:
            template_name: Template identifier ('render', 'source', 'publish', etc.)
            **kwargs: Template variables (project, shot, version, frame, etc.)

        Returns:
            Formatted path string

        Raises:
            ValueError: If template not found or invalid parameters
            RuntimeError: If lucidity not available
        """
        if not HAS_LUCIDITY:
            raise RuntimeError("Lucidity not available - cannot format paths")

        if template_name not in self.templates:
            available = ', '.join(self.templates.keys())
            raise ValueError(f"Unknown template: {template_name}. Available: {available}")

        if 'root' not in kwargs:
            kwargs['root'] = self.root_path

        try:
            template = self.templates[template_name]
            path = template.format(kwargs)
            return path

        except KeyError as e:
            raise ValueError(f"Missing required parameter: {e}")
        except Exception as e:
            logging.exception(f"Template formatting error: {e}")
            raise ValueError(f"Invalid template parameters for '{template_name}': {e}")

    def parse_path(self, template_name: str, path: str) -> Optional[Dict[str, Any]]:
        """
        Extract metadata from path using specific template.

        Args:
            template_name: Template to use for parsing
            path: Path string to parse

        Returns:
            Dictionary of extracted values or None if no match
        """
        if not HAS_LUCIDITY:
            logging.debug("Lucidity not available")
            return None

        if template_name not in self.templates:
            logging.warning(f"Unknown template: {template_name}")
            return None

        try:
            template = self.templates[template_name]
            result = template.parse(path)

            if result:
                result['_template'] = template_name
                logging.debug(f"Parsed {path} using template '{template_name}'")

            return result

        except Exception as e:
            logging.debug(f"Parse failed for template '{template_name}': {e}")
            return None

    def parse_any(self, path: str) -> Optional[Dict[str, Any]]:
        """
        Try parsing path against all templates.

        Args:
            path: Path string to parse

        Returns:
            Dictionary with first match or None if no templates match
        """
        if not HAS_LUCIDITY:
            return None

        for template_name in self.templates.keys():
            result = self.parse_path(template_name, path)
            if result:
                return result

        logging.debug(f"No template matched path: {path}")
        return None

    def validate_path(self, template_name: str, path: str) -> bool:
        """
        Check if a path matches a specific template.

        Args:
            template_name: Template to validate against
            path: Path string to validate

        Returns:
            True if path matches template, False otherwise
        """
        result = self.parse_path(template_name, path)
        return result is not None

    def get_template_fields(self, template_name: str) -> List[str]:
        """
        Get list of required fields for a template.

        Args:
            template_name: Template identifier

        Returns:
            List of field names
        """
        if not HAS_LUCIDITY:
            return []

        if template_name not in self.templates:
            return []

        try:
            template = self.templates[template_name]
            import re
            pattern = template.pattern
            fields = re.findall(r'\{(\w+)', pattern)
            return fields
        except Exception as e:
            logging.exception(f"Error getting template fields: {e}")
            return []

    def list_templates(self) -> List[str]:
        """Get list of available template names."""
        return list(self.templates.keys())

    def add_custom_template(self, name: str, pattern: str):
        """
        Add a custom template at runtime.

        Args:
            name: Template identifier
            pattern: Lucidity pattern string
        """
        if not HAS_LUCIDITY:
            raise RuntimeError("Lucidity not available")

        try:
            self.templates[name] = lucidity.Template(name, pattern)
            logging.info(f"Added custom template: {name}")
        except Exception as e:
            logging.exception(f"Failed to add template '{name}': {e}")
            raise ValueError(f"Invalid template pattern: {e}")


_global_manager = None


def get_path_manager(root_path: Optional[str] = None) -> PathTemplateManager:
    """
    Get global path template manager instance (singleton).

    Args:
        root_path: Optional root path (only used on first call)

    Returns:
        PathTemplateManager instance
    """
    global _global_manager

    if _global_manager is None:
        _global_manager = PathTemplateManager(root_path)

    return _global_manager


def format_render_path(project: str, sequence: str, shot: str,
                       task: str, version: int, frame: int,
                       root: Optional[str] = None) -> str:
    """
    Quick helper for render path generation.
    """
    mgr = get_path_manager(root)
    return mgr.format_path('render',
        project=project, sequence=sequence, shot=shot,
        task=task, version=version, frame=frame
    )


def parse_render_path(path: str) -> Optional[Dict[str, Any]]:
    """
    Quick helper for render path parsing.
    """
    mgr = get_path_manager()
    return mgr.parse_path('render', path)
