"""
SQLAlchemy 2.0 Operational Models for Slate VFX.
Covers Stock Library, HRMS (Attendance, Leave, Onboarding), IT (Hardware, Deployments, Tickets, Licenses),
and Production (Scheduling, Bidding).
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Optional
from sqlalchemy import (BigInteger, Boolean, Date, DateTime, Float, ForeignKey, Integer, Numeric,
                        String, Text, UniqueConstraint)
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
    """
    Production milestone scheduling mapping to 'prod_scheduling'.

    The columns production_schema creates on both backends. Not used at run
    time (the repository writes SQL); kept in step for readers and Alembic.
    """
    __tablename__ = "prod_scheduling"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_code: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    milestone: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    start_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    end_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    status: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    depends_on_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("prod_scheduling.id", ondelete="SET NULL"), nullable=True)
    owner: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    department: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    effort_days: Mapped[Optional[Decimal]] = mapped_column(Numeric(8, 2), nullable=True)
    completed_on: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    legacy_dates: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_by: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    updated_by: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class ProdBiddingModel(Base):
    """
    Production bidding and cost estimation mapping to 'prod_bidding'.

    Money is NUMERIC (estimated_budget was REAL - float4 - and lost cents).
    The old SQLite-only cost_per_shot column stays in those databases but is
    not used, so it is not listed.
    """
    __tablename__ = "prod_bidding"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_code: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, index=True)
    project_name: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    client_name: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    shot_count: Mapped[Optional[int]] = mapped_column(Integer, default=0)
    complexity: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    estimated_days: Mapped[Optional[Decimal]] = mapped_column(Numeric(15, 2), nullable=True)
    target_margin: Mapped[Optional[Decimal]] = mapped_column(Numeric(5, 2), nullable=True)
    estimated_cost: Mapped[Optional[Decimal]] = mapped_column(Numeric(15, 2), nullable=True)
    estimated_budget: Mapped[Optional[Decimal]] = mapped_column(Numeric(15, 2), nullable=True)
    status: Mapped[str] = mapped_column(String(64), default="Draft")
    currency: Mapped[Optional[str]] = mapped_column(String(3), nullable=True)
    day_rate: Mapped[Optional[Decimal]] = mapped_column(Numeric(15, 2), nullable=True)
    discount_percent: Mapped[Optional[Decimal]] = mapped_column(Numeric(5, 2), nullable=True)
    tax_percent: Mapped[Optional[Decimal]] = mapped_column(Numeric(5, 2), nullable=True)
    tax_label: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    tax_amount: Mapped[Optional[Decimal]] = mapped_column(Numeric(15, 2), nullable=True)
    total_amount: Mapped[Optional[Decimal]] = mapped_column(Numeric(15, 2), nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    bid_group: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    revision: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    created_by: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    updated_by: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    decided_by: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    decided_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    archived_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    archived_by: Mapped[Optional[str]] = mapped_column(Text, nullable=True)


class ProdBidLineModel(Base):
    """One line item of a bid, mapping to 'prod_bid_lines'."""
    __tablename__ = "prod_bid_lines"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    bid_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("prod_bidding.id", ondelete="CASCADE"), nullable=False, index=True)
    position: Mapped[int] = mapped_column(Integer, default=0)
    label: Mapped[str] = mapped_column(Text, default="")
    shot_name: Mapped[Optional[str]] = mapped_column(Text, default="")
    reel: Mapped[Optional[str]] = mapped_column(Text, default="")
    department: Mapped[Optional[str]] = mapped_column(Text, default="")
    complexity: Mapped[Optional[str]] = mapped_column(Text, default="")
    shot_count: Mapped[int] = mapped_column(Integer, default=1)
    days_per_shot: Mapped[Decimal] = mapped_column(Numeric(8, 2), default=0)
    day_rate: Mapped[Decimal] = mapped_column(Numeric(15, 2), default=0)
    days: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)
    cost: Mapped[Decimal] = mapped_column(Numeric(15, 2), default=0)
    notes: Mapped[Optional[str]] = mapped_column(Text, default="")
