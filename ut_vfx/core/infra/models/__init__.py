"""
SQLAlchemy 2.0 Models for UTCAP VFX.
Exports all Declarative Base classes, Mixins, and Relational Entities.
"""

from ut_vfx.core.infra.models.base import Base, TimestampMixin, VersionMixin

from ut_vfx.core.infra.models.auth import (
    UserModel,
    RoleModel,
    AuditLogModel,
)

from ut_vfx.core.infra.models.tracking import (
    ProjectModel,
    ShotModel,
    TaskModel,
    ChangeHistoryModel,
    LegacyProjectModel,
    LegacyOperationModel,
    LegacyTaskDetailModel,
)

from ut_vfx.core.infra.models.operations import (
    StockAssetModel,
    AttendanceLogModel,
    HardwareInventoryModel,
    ItDeploymentModel,
    ItTicketModel,
    TicketCommentModel,
    ItLicenseModel,
    LeaveRequestModel,
    LeaveBalanceModel,
    OnboardingWorkflowModel,
    ProdSchedulingModel,
    ProdBiddingModel,
)

__all__ = [
    "Base",
    "TimestampMixin",
    "VersionMixin",
    "UserModel",
    "RoleModel",
    "AuditLogModel",
    "ProjectModel",
    "ShotModel",
    "TaskModel",
    "ChangeHistoryModel",
    "LegacyProjectModel",
    "LegacyOperationModel",
    "LegacyTaskDetailModel",
    "StockAssetModel",
    "AttendanceLogModel",
    "HardwareInventoryModel",
    "ItDeploymentModel",
    "ItTicketModel",
    "TicketCommentModel",
    "ItLicenseModel",
    "LeaveRequestModel",
    "LeaveBalanceModel",
    "OnboardingWorkflowModel",
    "ProdSchedulingModel",
    "ProdBiddingModel",
]
