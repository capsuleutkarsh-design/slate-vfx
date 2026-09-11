"""convert_data_json_to_jsonb

Revision ID: b6305c8549ef
Revises: 
Create Date: 2026-06-05 17:53:26.145882

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b6305c8549ef'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Drop defaults first
    op.execute('ALTER TABLE tracking_shots ALTER COLUMN data_json DROP DEFAULT')
    op.execute('ALTER TABLE tracking_shots ALTER COLUMN data_json TYPE JSONB USING data_json::jsonb')
    op.execute("ALTER TABLE tracking_shots ALTER COLUMN data_json SET DEFAULT '{}'::jsonb")
    
    op.execute('ALTER TABLE tracking_projects ALTER COLUMN config_json DROP DEFAULT')
    op.execute('ALTER TABLE tracking_projects ALTER COLUMN config_json TYPE JSONB USING config_json::jsonb')
    op.execute("ALTER TABLE tracking_projects ALTER COLUMN config_json SET DEFAULT '{}'::jsonb")

def downgrade() -> None:
    """Downgrade schema."""
    op.execute('ALTER TABLE tracking_shots ALTER COLUMN data_json TYPE TEXT USING data_json::text')
    op.execute('ALTER TABLE tracking_projects ALTER COLUMN config_json TYPE TEXT USING config_json::text')
