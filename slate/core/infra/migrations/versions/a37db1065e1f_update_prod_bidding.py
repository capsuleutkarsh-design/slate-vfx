"""update_prod_bidding

Revision ID: a37db1065e1f
Revises: 794bc2e30631
Create Date: 2026-07-16 14:18:25.824141

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a37db1065e1f'
down_revision: Union[str, Sequence[str], None] = '794bc2e30631'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('prod_bidding', sa.Column('project_code', sa.String(255), nullable=True))
    op.add_column('prod_bidding', sa.Column('shot_count', sa.Integer(), server_default='0'))
    op.add_column('prod_bidding', sa.Column('complexity', sa.String(50), server_default='Medium'))
    op.add_column('prod_bidding', sa.Column('estimated_days', sa.Numeric(15, 2), server_default='0.0'))
    op.add_column('prod_bidding', sa.Column('target_margin', sa.Numeric(5, 2), server_default='20.0'))
    op.add_column('prod_bidding', sa.Column('estimated_cost', sa.Numeric(15, 2), server_default='0.0'))


def downgrade() -> None:
    pass
