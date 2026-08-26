"""add Phase C: recruiter tags, source subfields, duplicate linking

Revision ID: d2c17b771e7f
Revises: aa93d6677bd1
Create Date: 2026-08-26 14:09:35.588963

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd2c17b771e7f'
down_revision: Union[str, Sequence[str], None] = 'aa93d6677bd1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('recruiter_tags',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('candidate_id', sa.Integer(), nullable=True),
    sa.Column('tag', sa.String(), nullable=False),
    sa.Column('applied_by', sa.String(), nullable=True),
    sa.Column('applied_at', sa.DateTime(), nullable=True),
    sa.ForeignKeyConstraint(['candidate_id'], ['candidates.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    # batch_alter_table instead of plain op.add_column/op.create_foreign_key:
    # SQLite can't ALTER TABLE to add a constraint directly (only Postgres
    # can), so this uses Alembic's copy-and-move strategy on SQLite while
    # still emitting a normal ALTER TABLE on Postgres — same migration file
    # works correctly against both, which is what let this be tested locally
    # before running against production.
    with op.batch_alter_table('candidates', schema=None) as batch_op:
        batch_op.add_column(sa.Column('sub_source', sa.String(), nullable=True))
        batch_op.add_column(sa.Column('referral_employee', sa.String(), nullable=True))
        batch_op.add_column(sa.Column('agency_name', sa.String(), nullable=True))
        batch_op.add_column(sa.Column('is_duplicate_of', sa.Integer(), nullable=True))
        batch_op.create_foreign_key('fk_candidates_is_duplicate_of', 'candidates', ['is_duplicate_of'], ['id'])


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('candidates', schema=None) as batch_op:
        batch_op.drop_constraint('fk_candidates_is_duplicate_of', type_='foreignkey')
        batch_op.drop_column('is_duplicate_of')
        batch_op.drop_column('agency_name')
        batch_op.drop_column('referral_employee')
        batch_op.drop_column('sub_source')
    op.drop_table('recruiter_tags')
