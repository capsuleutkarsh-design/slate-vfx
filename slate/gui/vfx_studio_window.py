"""
Slate Studio - Production Window
=================================
Dedicated client window for VFX Artists, Leads, Coordinators, and Supervisors.
Houses the VFX Dashboard Pro, Shot Review & EXR Player, Stock Asset Browser,
Folder Creator, and Ingest / Renaming tools without HRMS/IT overhead.
"""

from .main_window import VFXFolderCreatorApp


class VFXStudioWindow(VFXFolderCreatorApp):
    """
    Lean, high-performance window tailored specifically for VFX production.
    """
    def __init__(self, user_data=None, app_context=None):
        self.app_mode = "vfx"
        super().__init__(user_data=user_data, app_context=app_context, app_mode="vfx")
