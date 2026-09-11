"""add_ticket_comments

Revision ID: e2e4025e12d7
Revises: a37db1065e1f
Create Date: 2026-07-16 14:41:39.712395

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e2e4025e12d7'
down_revision: Union[str, Sequence[str], None] = 'a37db1065e1f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'it_ticket_comments',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('ticket_id', sa.Integer(), nullable=False),
        sa.Column('author', sa.String(255), nullable=False),
        sa.Column('comment_text', sa.Text(), nullable=False),
        sa.Column('timestamp', sa.String(50), nullable=True),
        sa.PrimaryKeyConstraint('id')
    )


def downgrade() -> None:
    op.drop_table('it_ticket_comments')
