from decimal import Decimal

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.models import LedgerEntry, Wallet


def reconcile(db: Session) -> dict:
    """Check the ledger's invariants and report every violation found."""
    # Invariant 1: stored balance == sum of that wallet's entries.
    # One SQL statement, so it sees one consistent snapshot of the data.
    wallet_rows = (
        db.query(
            Wallet.id,
            Wallet.balance,
            func.coalesce(func.sum(LedgerEntry.amount), 0),
        )
        .outerjoin(LedgerEntry, LedgerEntry.wallet_id == Wallet.id)
        .group_by(Wallet.id, Wallet.balance)
        .all()
    )

    mismatched_wallets = [
        {"wallet_id": wallet_id, "balance": balance, "entries_sum": entries_sum}
        for wallet_id, balance, entries_sum in wallet_rows
        if balance != entries_sum
    ]

    # Invariant 3: all balances together (system wallet included) sum to zero.
    total_of_balances = sum((balance for _, balance, _ in wallet_rows), Decimal("0"))

    # Invariant 2: every transaction has exactly two entries that sum to zero.
    unbalanced_rows = (
        db.query(
            LedgerEntry.transaction_id,
            func.sum(LedgerEntry.amount),
            func.count(LedgerEntry.id),
        )
        .group_by(LedgerEntry.transaction_id)
        .having(
            or_(
                func.sum(LedgerEntry.amount) != 0,
                func.count(LedgerEntry.id) != 2,
            )
        )
        .all()
    )
    unbalanced_transactions = [
        {"transaction_id": transaction_id, "entries_sum": entries_sum, "entry_count": entry_count}
        for transaction_id, entries_sum, entry_count in unbalanced_rows
    ]

    return {
        "balanced": not mismatched_wallets
        and not unbalanced_transactions
        and total_of_balances == 0,
        "total_of_balances": total_of_balances,
        "wallets_checked": len(wallet_rows),
        "mismatched_wallets": mismatched_wallets,
        "unbalanced_transactions": unbalanced_transactions,
    }