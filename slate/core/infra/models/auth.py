"""
SQLAlchemy 2.0 Auth and User Management Models for Slate VFX.
Maps to ut_users, ut_roles, and audit_log tables.
"""

from datetime import datetime
import json
from typing import List, Optional
from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from slate.core.infra.models.base import Base, TimestampMixin


class UserModel(Base):
    """
    User entity mapping to 'ut_users'.
    Supports multi-role membership and password hashing.
    """
    __tablename__ = "ut_users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(128), unique=True, nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(String(256), default="", nullable=False)
    display_name: Mapped[str] = mapped_column(String(256), default="", nullable=False)
    job_title: Mapped[str] = mapped_column(String(128), default="", nullable=False)
    roles: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    profile_pic_path: Mapped[str] = mapped_column(String(512), default="", nullable=False)
    last_synced: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    @property
    def role_list(self) -> List[str]:
        """Parse roles from JSON string or legacy format."""
        if not self.roles:
            return []
        try:
            parsed = json.loads(self.roles)
            if isinstance(parsed, list):
                return [str(r).strip() for r in parsed if str(r).strip()]
            return [str(parsed).strip()]
        except Exception:
            return [r.strip() for r in self.roles.split(",") if r.strip()]

    @role_list.setter
    def role_list(self, roles: List[str]) -> None:
        """Serialize roles into a JSON list string."""
        clean_roles = [str(r).strip() for r in roles if str(r).strip()]
        self.roles = json.dumps(clean_roles)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "username": self.username,
            "display_name": self.display_name,
            "job_title": self.job_title,
            "roles": self.role_list,
            "profile_pic_path": self.profile_pic_path,
            "last_synced": self.last_synced,
        }


class RoleModel(Base):
    """
    Role definitions mapping to 'ut_roles'.
    """
    __tablename__ = "ut_roles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    role_name: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    permissions: Mapped[str] = mapped_column(Text, default="[]", nullable=False)

    @property
    def permission_list(self) -> List[str]:
        if not self.permissions:
            return []
        try:
            parsed = json.loads(self.permissions)
            if isinstance(parsed, list):
                return [str(p) for p in parsed]
            return [str(parsed)]
        except Exception:
            return [p.strip() for p in self.permissions.split(",") if p.strip()]

    @permission_list.setter
    def permission_list(self, perms: List[str]) -> None:
        self.permissions = json.dumps([str(p).strip() for p in perms])


class AuditLogModel(Base):
    """
    System-wide audit trail mapping to 'audit_log'.
    """
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    timestamp: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    user_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    action: Mapped[str] = mapped_column(String(128), default="", nullable=False)
    target: Mapped[str] = mapped_column(String(256), default="", nullable=False)
    details: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
