"""add_missing_tabs_tables

Revision ID: d75065634626
Revises: c54054523515
Create Date: 2026-07-16 12:40:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd75065634626'
down_revision: Union[str, Sequence[str], None] = 'c54054523515'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute('''
    -- Alter hardware_inventory
    ALTER TABLE hardware_inventory ADD COLUMN IF NOT EXISTS cpu VARCHAR(100);
    ALTER TABLE hardware_inventory ADD COLUMN IF NOT EXISTS storage VARCHAR(100);
    ALTER TABLE hardware_inventory ADD COLUMN IF NOT EXISTS type VARCHAR(50);

    -- Create it_licenses
    CREATE TABLE IF NOT EXISTS it_licenses (
        id SERIAL PRIMARY KEY,
        software_name VARCHAR(100) NOT NULL,
        license_key VARCHAR(255),
        seats_total INT DEFAULT 0,
        seats_used INT DEFAULT 0,
        expiry_date DATE
    );

    -- Create it_deployments
    CREATE TABLE IF NOT EXISTS it_deployments (
        id SERIAL PRIMARY KEY,
        package_name VARCHAR(255) NOT NULL,
        target_machine VARCHAR(100) NOT NULL,
        deployed_by VARCHAR(50),
        status VARCHAR(50) DEFAULT 'Pending',
        deployed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );

    -- Create prod_scheduling
    CREATE TABLE IF NOT EXISTS prod_scheduling (
        id SERIAL PRIMARY KEY,
        project_code VARCHAR(50) NOT NULL,
        milestone VARCHAR(255) NOT NULL,
        start_date DATE,
        end_date DATE,
        status VARCHAR(50) DEFAULT 'Scheduled'
    );

    -- Create prod_bidding
    CREATE TABLE IF NOT EXISTS prod_bidding (
        id SERIAL PRIMARY KEY,
        project_name VARCHAR(255) NOT NULL,
        client_name VARCHAR(255),
        estimated_budget NUMERIC(15, 2),
        status VARCHAR(50) DEFAULT 'Draft'
    );
    ''')


def downgrade() -> None:
    """Downgrade schema."""
    op.execute('''
    DROP TABLE IF EXISTS prod_bidding;
    DROP TABLE IF EXISTS prod_scheduling;
    DROP TABLE IF EXISTS it_deployments;
    DROP TABLE IF EXISTS it_licenses;
    ALTER TABLE hardware_inventory DROP COLUMN IF EXISTS cpu;
    ALTER TABLE hardware_inventory DROP COLUMN IF EXISTS storage;
    ALTER TABLE hardware_inventory DROP COLUMN IF EXISTS type;
    ''')
