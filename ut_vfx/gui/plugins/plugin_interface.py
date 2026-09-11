from PySide6.QtWidgets import QWidget


class UTVFXPlugin(QWidget):
    """Base class for UT_VFX plugins loaded as tabs."""

    def __init__(self, parent=None):
        super().__init__(parent)

    @property
    def plugin_name(self) -> str:
        """Name shown in tab header."""
        return "Unknown Plugin"

    @property
    def plugin_icon(self) -> str:
        """Short icon text shown in tab header."""
        return "P"

    def initialize(self, context: dict):
        """
        Called after instantiation to inject application context.
        Context may contain: user_data, config_manager, main_window.
        """

    def on_load(self):
        """Called when plugin is loaded (optional)."""

    def on_unload(self):
        """Called when plugin is unloaded (optional)."""
