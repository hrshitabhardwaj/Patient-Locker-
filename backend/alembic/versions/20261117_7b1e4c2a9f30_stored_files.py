"""stored files (encrypted documents kept in the database)

Revision ID: 7b1e4c2a9f30
Revises: 540b63dbf053
Create Date: 2026-11-17 12:00:00
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '7b1e4c2a9f30'
down_revision: str | None = '540b63dbf053'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'stored_files',
        sa.Column('storage_key', sa.String(length=64), nullable=False),
        sa.Column('blob', sa.LargeBinary(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('storage_key', name=op.f('pk_stored_files')),
    )


def downgrade() -> None:
    op.drop_table('stored_files')
