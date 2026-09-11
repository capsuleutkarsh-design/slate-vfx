"""
SQLAlchemy 2.0 VFX Production Tracking Models for Slate VFX.
Maps to tracking_projects, tracking_shots, tracking_tasks, and change_history.
Includes VersionMixin for Optimistic Concurrency Control (OCC).
"""

import json
from typing import Any, Dict, List, Optional
from sqlalchemy import (
    BigInteger, Float, ForeignKey, Integer, String, Text, UniqueConstraint
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from slate.core.infra.models.base import Base, VersionMixin


class ProjectModel(Base):
    """
    Project entity mapping to 'tracking_projects'.
    """
    __tablename__ = "tracking_projects"

    code: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(256), default="", nullable=False)
    config_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    last_updated: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    active: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    # Relationships
    shots: Mapped[List["ShotModel"]] = relationship(
        "ShotModel", 
        back_populates="project",
        cascade="all, delete-orphan",
        primaryjoin="ProjectModel.code==ShotModel.project_code",
        foreign_keys="[ShotModel.project_code]"
    )

    @property
    def config(self) -> Dict[str, Any]:
        try:
            return json.loads(self.config_json) if self.config_json else {}
        except Exception:
            return {}

    @config.setter
    def config(self, val: Dict[str, Any]) -> None:
        self.config_json = json.dumps(val)

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "name": self.name,
            "config": self.config,
            "last_updated": self.last_updated,
            "active": bool(self.active),
        }


class ShotModel(Base, VersionMixin):
    """
    Shot tracking entity mapping to 'tracking_shots'.
    Supports OCC via VersionMixin.version column.
    """
    __tablename__ = "tracking_shots"
    __table_args__ = (
        UniqueConstraint("project_code", "shot_name", name="uq_tracking_shots_proj_shot"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_code: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    shot_name: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    priority: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    data_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    last_updated: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    # version inherited from VersionMixin: Mapped[int], default=1

    # Relationships
    project: Mapped[Optional[ProjectModel]] = relationship(
        "ProjectModel", 
        back_populates="shots",
        primaryjoin="ProjectModel.code==ShotModel.project_code",
        foreign_keys="[ShotModel.project_code]"
    )
    tasks: Mapped[List["TaskModel"]] = relationship(
        "TaskModel", 
        back_populates="shot",
        cascade="all, delete-orphan",
        primaryjoin="ShotModel.id==TaskModel.shot_id",
        foreign_keys="[TaskModel.shot_id]"
    )

    @property
    def data(self) -> Dict[str, Any]:
        try:
            return json.loads(self.data_json) if self.data_json else {}
        except Exception:
            return {}

    @data.setter
    def data(self, val: Dict[str, Any]) -> None:
        self.data_json = json.dumps(val)

    def to_dict(self) -> dict:
        d = dict(self.data)
        d.update({
            "id": self.id,
            "project_code": self.project_code,
            "shot_name": self.shot_name,
            "status": self.status,
            "priority": self.priority,
            "last_updated": self.last_updated,
            "version": self.version,
        })
        return d


class TaskModel(Base):
    """
    Departmental task entity mapping to 'tracking_tasks'.
    """
    __tablename__ = "tracking_tasks"
    __table_args__ = (
        UniqueConstraint("project_code", "shot_id", "department", name="uq_tracking_tasks_proj_shot_dept"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    shot_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    project_code: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    department: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    status: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    artist_name: Mapped[str] = mapped_column(String(128), default="", nullable=False)
    artist_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    bid_days: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    target_date: Mapped[str] = mapped_column(String(64), default="", nullable=False)

    shot: Mapped[Optional[ShotModel]] = relationship(
        "ShotModel", 
        back_populates="tasks",
        primaryjoin="ShotModel.id==TaskModel.shot_id",
        foreign_keys="[TaskModel.shot_id]"
    )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "shot_id": self.shot_id,
            "project_code": self.project_code,
            "department": self.department,
            "status": self.status,
            "artist_name": self.artist_name,
            "artist_id": self.artist_id,
            "bid_days": self.bid_days,
            "target_date": self.target_date,
        }


class ChangeHistoryModel(Base):
    """
    Audit and version history for tracking modifications mapping to 'change_history'.
    """
    __tablename__ = "change_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_code: Mapped[str] = mapped_column(String(64), default="", nullable=False, index=True)
    entity_type: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    entity_id: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    user_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    action_type: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    field_changed: Mapped[str] = mapped_column(String(128), default="", nullable=False)
    old_value: Mapped[str] = mapped_column(Text, default="", nullable=False)
    new_value: Mapped[str] = mapped_column(Text, default="", nullable=False)
    timestamp: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)


# Legacy directory-creation tracking tables
class LegacyProjectModel(Base):
    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    template_used: Mapped[str] = mapped_column(String(256), default="")
    target_directory: Mapped[str] = mapped_column(String(512), default="")
    total_folders: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)


class LegacyOperationModel(Base):
    __tablename__ = "operations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    operation_type: Mapped[str] = mapped_column(String(64), default="")
    start_time: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    end_time: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    duration: Mapped[float] = mapped_column(Float, default=0.0)
    items_processed: Mapped[int] = mapped_column(Integer, default=0)
    errors: Mapped[int] = mapped_column(Integer, default=0)
    success: Mapped[int] = mapped_column(Integer, default=0)


class LegacyTaskDetailModel(Base):
    __tablename__ = "task_details"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    operation_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    item_name: Mapped[str] = mapped_column(String(256), default="")
    source_path: Mapped[str] = mapped_column(String(512), default="")
    dest_path: Mapped[str] = mapped_column(String(512), default="")
    file_size: Mapped[int] = mapped_column(BigInteger, default=0)
    duration: Mapped[float] = mapped_column(Float, default=0.0)
    status: Mapped[str] = mapped_column(String(64), default="")
    error_msg: Mapped[str] = mapped_column(Text, default="")
    timestamp: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
