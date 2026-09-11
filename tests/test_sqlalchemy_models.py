"""
Comprehensive unit tests for SQLAlchemy 2.0 models and DatabaseFactory.
"""

import os
import sys
import unittest
import tempfile
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from ut_vfx.core.infra.models.base import Base
from ut_vfx.core.infra.models.auth import UserModel, RoleModel, AuditLogModel
from ut_vfx.core.infra.models.tracking import ProjectModel, ShotModel, TaskModel, ChangeHistoryModel
from ut_vfx.core.infra.models.operations import (
    StockAssetModel, AttendanceLogModel, HardwareInventoryModel,
    ItDeploymentModel, ItTicketModel, LeaveRequestModel, LeaveBalanceModel
)
from ut_vfx.core.infra.db_factory import get_db_session, init_schema


class TestSQLAlchemyModels(unittest.TestCase):
    """Test suite for Declarative Base entities and relationships."""

    @classmethod
    def setUpClass(cls):
        # Create an in-memory SQLite database with Base schema
        cls.engine = create_engine("sqlite:///:memory:", echo=False)
        Base.metadata.create_all(cls.engine)
        cls.Session = sessionmaker(bind=cls.engine)

    def setUp(self):
        self.session = self.Session()

    def tearDown(self):
        self.session.rollback()
        self.session.close()

    def test_user_model_crud_and_roles(self):
        user = UserModel(
            username="vfx_artist_1",
            password_hash="argon2_hashed_secret",
            display_name="Jane Doe",
            job_title="Lead Compositor",
        )
        user.role_list = ["Artist", "CompLead"]
        self.session.add(user)
        self.session.commit()

        queried = self.session.scalar(select(UserModel).where(UserModel.username == "vfx_artist_1"))
        self.assertIsNotNone(queried)
        self.assertEqual(queried.display_name, "Jane Doe")
        self.assertEqual(queried.role_list, ["Artist", "CompLead"])
        self.assertIn("Artist", queried.roles)

    def test_role_model_crud(self):
        role = RoleModel(role_name="Supervisor")
        role.permission_list = ["shots.edit", "shots.approve", "tasks.assign"]
        self.session.add(role)
        self.session.commit()

        queried = self.session.scalar(select(RoleModel).where(RoleModel.role_name == "Supervisor"))
        self.assertIsNotNone(queried)
        self.assertEqual(queried.permission_list, ["shots.edit", "shots.approve", "tasks.assign"])

    def test_project_and_shot_relationship_and_occ(self):
        proj = ProjectModel(code="PRJ_TEST", name="Test Film Project")
        proj.config = {"fps": 24, "resolution": "3840x2160"}
        self.session.add(proj)
        self.session.commit()

        shot = ShotModel(
            project_code="PRJ_TEST",
            shot_name="SH_010",
            status="In Progress",
            priority=1,
            version=1
        )
        shot.data = {"frame_in": 1001, "frame_out": 1085, "handles": 8}
        self.session.add(shot)
        self.session.commit()

        # Query and test OCC
        queried_shot = self.session.scalar(select(ShotModel).where(ShotModel.shot_name == "SH_010"))
        self.assertIsNotNone(queried_shot)
        self.assertEqual(queried_shot.version, 1)
        self.assertEqual(queried_shot.data.get("frame_in"), 1001)

        # Update version
        queried_shot.version += 1
        self.session.commit()

        updated_shot = self.session.scalar(select(ShotModel).where(ShotModel.shot_name == "SH_010"))
        self.assertEqual(updated_shot.version, 2)

    def test_task_model_foreign_relationship(self):
        shot = ShotModel(project_code="PRJ_TASK", shot_name="SH_TASK_01", status="Ready", version=1)
        self.session.add(shot)
        self.session.commit()

        task = TaskModel(
            shot_id=shot.id,
            project_code="PRJ_TASK",
            department="comp",
            status="In Progress",
            artist_name="Alex Turner",
            bid_days=3.5,
            target_date="2026-09-15"
        )
        self.session.add(task)
        self.session.commit()

        queried_task = self.session.scalar(select(TaskModel).where(TaskModel.shot_id == shot.id))
        self.assertIsNotNone(queried_task)
        self.assertEqual(queried_task.department, "comp")
        self.assertEqual(queried_task.bid_days, 3.5)

    def test_operations_models_persistence(self):
        # Stock asset
        stock = StockAssetModel(
            file_path="//storage/vfx/stock/fire_01.exr",
            file_name="fire_01.exr",
            file_type="exr",
            file_size=104857600
        )
        self.session.add(stock)

        # Hardware inventory
        hw = HardwareInventoryModel(
            machine_name="WS-VFX-042",
            assigned_to="alex_turner",
            gpu="RTX 4090",
            cpu="Ryzen 9 7950X",
            ram="128 GB",
            status="Active"
        )
        self.session.add(hw)

        # Leave balance
        leave_bal = LeaveBalanceModel(
            user_id="alex_turner",
            cl_balance=10.0,
            sl_balance=5.0,
            el_balance=15.0
        )
        self.session.add(leave_bal)

        self.session.commit()

        q_stock = self.session.scalar(select(StockAssetModel).where(StockAssetModel.file_name == "fire_01.exr"))
        self.assertIsNotNone(q_stock)
        self.assertEqual(q_stock.file_size, 104857600)

        q_hw = self.session.scalar(select(HardwareInventoryModel).where(HardwareInventoryModel.machine_name == "WS-VFX-042"))
        self.assertIsNotNone(q_hw)
        self.assertEqual(q_hw.gpu, "RTX 4090")


if __name__ == "__main__":
    unittest.main()
