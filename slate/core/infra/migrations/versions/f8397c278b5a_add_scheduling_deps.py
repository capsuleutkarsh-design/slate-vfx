"""add_scheduling_deps

Revision ID: f8397c278b5a
Revises: e2e4025e12d7
Create Date: 2026-07-16 14:48:35.263581

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f8397c278b5a'
down_revision: Union[str, Sequence[str], None] = 'e2e4025e12d7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # prod_scheduling is created by postgres_manager with this column already
    # on it, so a bare ADD COLUMN aborts on any database that has ever had a
    # client connect to it. Alembic then stamps no version, which leaves it
    # permanently unable to apply this or any later revision - the failure is
    # in the log and nowhere else.
    op.execute("ALTER TABLE prod_scheduling "
               "ADD COLUMN IF NOT EXISTS depends_on_id INTEGER "
               "REFERENCES prod_scheduling(id)")


def downgrade() -> None:
    op.execute("ALTER TABLE prod_scheduling DROP COLUMN IF EXISTS depends_on_id")
