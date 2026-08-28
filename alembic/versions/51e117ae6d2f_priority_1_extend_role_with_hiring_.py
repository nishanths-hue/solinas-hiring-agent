"""Priority 1: extend Role with Hiring Request fields

Revision ID: 51e117ae6d2f
Revises: a1783edf671a
Create Date: 2026-08-28 07:56:45.637063

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '51e117ae6d2f'
down_revision: Union[str, Sequence[str], None] = 'a1783edf671a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # batch_alter_table, not plain op.add_column/op.create_unique_constraint —
    # SQLite can't ALTER TABLE to add a unique constraint directly (only
    # Postgres can), so this uses Alembic's copy-and-move strategy on
    # SQLite while still emitting a normal ALTER TABLE on Postgres. Same
    # fix as the earlier is_duplicate_of migration — this is the second
    # time autogenerate has produced this exact pattern.
    with op.batch_alter_table('roles', schema=None) as batch_op:
        batch_op.add_column(sa.Column('location', sa.String(), nullable=True))
        batch_op.add_column(sa.Column('work_mode', sa.String(), nullable=True))
        batch_op.add_column(sa.Column('employment_type', sa.String(), nullable=True))
        batch_op.add_column(sa.Column('budget', sa.String(), nullable=True))
        batch_op.add_column(sa.Column('request_display_id', sa.String(), nullable=True))
        batch_op.create_unique_constraint('uq_roles_request_display_id', ['request_display_id'])


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('roles', schema=None) as batch_op:
        batch_op.drop_constraint('uq_roles_request_display_id', type_='unique')
        batch_op.drop_column('request_display_id')
        batch_op.drop_column('budget')
        batch_op.drop_column('employment_type')
        batch_op.drop_column('work_mode')
        batch_op.drop_column('location')
