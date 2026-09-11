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
    op.add_column('prod_scheduling', sa.Column('depends_on_id', sa.Integer(), sa.ForeignKey('prod_scheduling.id'), nullable=True))


def downgrade() -> None:
    op.drop_column('prod_scheduling', 'depends_on_id')
