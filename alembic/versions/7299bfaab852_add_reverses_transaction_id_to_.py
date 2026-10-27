"""add reverses_transaction_id to transactions

Revision ID: 7299bfaab852
Revises: 453abca0af0d
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '7299bfaab852'
down_revision: Union[str, Sequence[str], None] = '453abca0af0d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('transactions', sa.Column('reverses_transaction_id', sa.UUID(), nullable=True))
    op.create_unique_constraint(
        'transactions_reverses_transaction_id_key', 'transactions', ['reverses_transaction_id']
    )
    op.create_foreign_key(
        'transactions_reverses_transaction_id_fkey',
        'transactions', 'transactions', ['reverses_transaction_id'], ['id'],
    )


def downgrade() -> None:
    op.drop_constraint('transactions_reverses_transaction_id_fkey', 'transactions', type_='foreignkey')
    op.drop_constraint('transactions_reverses_transaction_id_key', 'transactions', type_='unique')
    op.drop_column('transactions', 'reverses_transaction_id')