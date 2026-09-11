"""
UT Studio Operations - Management Portal Window
===============================================
Dedicated client window for Studio Producers, HR Management, and IT Administrators.
Houses Biometric Attendance & Shifts, Leaves & PTO, Onboarding, IT Inventory,
DCC Licenses, Helpdesk Ticketing, Automated Deployments, and User RBAC Management.
"""

from .main_window import VFXFolderCreatorApp


class StudioOpsWindow(VFXFolderCreatorApp):
    """
    Specialized management portal tailored for HRMS, IT, and Studio Operations.
    """
    def __init__(self, user_data=None, app_context=None):
        self.app_mode = "ops"
        super().__init__(user_data=user_data, app_context=app_context, app_mode="ops")
