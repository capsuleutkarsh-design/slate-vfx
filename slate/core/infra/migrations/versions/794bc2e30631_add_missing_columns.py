"""add_missing_columns

Revision ID: 794bc2e30631
Revises: d75065634626
Create Date: 2026-07-16 14:08:58.278021

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '794bc2e30631'
down_revision: Union[str, Sequence[str], None] = 'd75065634626'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('hardware_inventory', sa.Column('location', sa.String(255), nullable=True, server_default='N/A'))
    op.add_column('leave_requests', sa.Column('half_day', sa.Integer(), nullable=True, server_default='0'))
    op.add_column('leave_requests', sa.Column('reason', sa.Text(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('leave_requests', 'reason')
    op.drop_column('leave_requests', 'half_day')
    op.drop_column('hardware_inventory', 'location')
