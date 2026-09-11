"""
SQLAlchemy 2.0 Operational Models for Slate VFX.
Covers Stock Library, HRMS (Attendance, Leave, Onboarding), IT (Hardware, Deployments, Tickets, Licenses),
and Production (Scheduling, Bidding).
"""

from typing import Optional
from sqlalchemy import BigInteger, Boolean, Float, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from slate.core.infra.models.base import Base


class StockAssetModel(Base):
    """Stock assets library mapping to 'stock_library'."""
    __tablename__ = "stock_library"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    file_path: Mapped[str] = mapped_column(String(512), unique=True, nullable=False, index=True)
    file_name: Mapped[str] = mapped_column(String(256), default="")
    file_size: Mapped[int] = mapped_column(BigInteger, default=0)
    file_type: Mapped[str] = mapped_column(String(32), default="")
    thumb_path: Mapped[str] = mapped_column(String(512), default="")
    proxy_path: Mapped[str] = mapped_column(String(512), default="")
    tags: Mapped[str] = mapped_column(Text, default="")
    metadata_json: Mapped[str] = mapped_column("metadata", Text, default="{}")
    embedding: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    ingest_date: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)


class AttendanceLogModel(Base):
    """Attendance log mapping to 'attendance_log'."""
    __tablename__ = "attendance_log"
    __table_args__ = (
        UniqueConstraint("user_id", "day_date", name="uq_attendance_user_day"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    day_date: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    punch_in: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    punch_out: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    pc_name: Mapped[str] = mapped_column(String(128), default="")
    metadata_json: Mapped[str] = mapped_column("metadata", Text, default="{}")


class HardwareInventoryModel(Base):
    """IT Hardware assets mapping to 'hardware_inventory'."""
    __tablename__ = "hardware_inventory"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    machine_name: Mapped[str] = mapped_column(String(128), unique=True, nullable=False, index=True)
    assigned_to: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    gpu: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    ram: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    cpu: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    storage: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    type: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(64), default="Active")
    location: Mapped[str] = mapped_column(String(128), default="")
    last_seen: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)


class ItDeploymentModel(Base):
    """IT Deployments mapping to 'it_deployments'."""
    __tablename__ = "it_deployments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    package_name: Mapped[str] = mapped_column(String(256), nullable=False)
    target_machine: Mapped[str] = mapped_column(String(128), nullable=False)
    deployed_by: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    status: Mapped[str] = mapped_column(String(64), default="Pending")
    deployed_at: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)


class ItTicketModel(Base):
    """IT Helpdesk tickets mapping to 'it_tickets'."""
    __tablename__ = "it_tickets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    submitted_by: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    category: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(64), default="Open")
    priority: Mapped[str] = mapped_column(String(64), default="Medium")
    assigned_to: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    created_at: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    resolved_at: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)


class TicketCommentModel(Base):
    """IT Ticket comments mapping to 'ticket_comments'."""
    __tablename__ = "ticket_comments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ticket_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    user_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    comment: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    timestamp: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)


class ItLicenseModel(Base):
    """Software licenses mapping to 'it_licenses'."""
    __tablename__ = "it_licenses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    software_name: Mapped[str] = mapped_column(String(128), nullable=False)
    license_key: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    seats_total: Mapped[int] = mapped_column(Integer, default=0)
    seats_used: Mapped[int] = mapped_column(Integer, default=0)
    expiry_date: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)


class LeaveRequestModel(Base):
    """HRMS Leave requests mapping to 'leave_requests'."""
    __tablename__ = "leave_requests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    type: Mapped[str] = mapped_column(String(64), nullable=False)
    start_date: Mapped[str] = mapped_column(String(32), nullable=False)
    end_date: Mapped[str] = mapped_column(String(32), nullable=False)
    half_day: Mapped[bool] = mapped_column(Boolean, default=False)
    reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(64), default="Pending")
    approved_by: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    created_at: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)


class LeaveBalanceModel(Base):
    """HRMS Leave balance tracking mapping to 'leave_balances'."""
    __tablename__ = "leave_balances"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(128), unique=True, nullable=False, index=True)
    cl_balance: Mapped[float] = mapped_column(Float, default=10.0)
    sl_balance: Mapped[float] = mapped_column(Float, default=5.0)
    el_balance: Mapped[float] = mapped_column(Float, default=5.0)
    lwp_balance: Mapped[float] = mapped_column(Float, default=0.0)
    last_updated: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)


class OnboardingWorkflowModel(Base):
    """HRMS Employee onboarding tasks mapping to 'onboarding_workflows'."""
    __tablename__ = "onboarding_workflows"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    task_name: Mapped[str] = mapped_column(String(256), nullable=False)
    department: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    is_completed: Mapped[bool] = mapped_column(Boolean, default=False)


class ProdSchedulingModel(Base):
    """Production milestone scheduling mapping to 'prod_scheduling'."""
    __tablename__ = "prod_scheduling"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_code: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    milestone: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    start_date: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    end_date: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    status: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)


class ProdBiddingModel(Base):
    """Production bidding and cost estimation mapping to 'prod_bidding'."""
    __tablename__ = "prod_bidding"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_code: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    shot_count: Mapped[int] = mapped_column(Integer, default=0)
    cost_per_shot: Mapped[float] = mapped_column(Float, default=0.0)
    estimated_budget: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String(64), default="Draft")
