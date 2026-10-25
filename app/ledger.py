import uuid
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.fraud import check_fraud
from app.models import FraudFlag, LedgerEntry, Transaction, Wallet


def find_by_key(db: Session, idempotency_key: uuid.UUID):
    return (
        db.query(Transaction)
        .filter(Transaction.idempotency_key == str(idempotency_key))
        .first()
    )


def replay_if_same_request(existing, sender_wallet_id, receiver_wallet_id, amount):
    """A key may only replay the exact request it was first used for."""
    same_request = (
        existing.sender_wallet_id == sender_wallet_id
        and existing.receiver_wallet_id == receiver_wallet_id
        and existing.amount == amount
    )
    if not same_request:
        raise HTTPException(
            status_code=422,
            detail="Idempotency key was already used for a different request",
        )
    return existing


def move_money(
    db: Session,
    sender_wallet_id: uuid.UUID,
    receiver_wallet_id: uuid.UUID,
    amount: Decimal,
    idempotency_key: uuid.UUID,
) -> Transaction:
    """Move money between two wallets.

    Atomic, idempotent, safe under concurrency, and writes two signed ledger
    entries that always sum to zero.
    """
    # Lock both wallets in one query, always in id order, so two transfers can
    # never hold locks in opposite orders (no deadlocks). populate_existing()
    # makes sure the freshly locked values replace anything cached.
    locked_wallets = (
        db.query(Wallet)
        .filter(Wallet.id.in_([sender_wallet_id, receiver_wallet_id]))
        .order_by(Wallet.id)
        .with_for_update()
        .populate_existing()
        .all()
    )
    wallets = {wallet.id: wallet for wallet in locked_wallets}
    sender_wallet = wallets[sender_wallet_id]
    receiver_wallet = wallets[receiver_wallet_id]

    # Checked AFTER the locks are held: if an identical request was racing us,
    # the winner has already committed by the time we get here.
    existing = find_by_key(db, idempotency_key)
    if existing:
        return replay_if_same_request(
            existing, sender_wallet_id, receiver_wallet_id, amount
        )

    if sender_wallet.balance < amount:
        raise HTTPException(status_code=400, detail="Insufficient balance")

    fraud_reason = check_fraud(db, sender_wallet_id, amount)

    sender_wallet.balance -= amount
    sender_wallet.version += 1

    receiver_wallet.balance += amount
    receiver_wallet.version += 1

    new_transaction = Transaction(
        idempotency_key=str(idempotency_key),
        sender_wallet_id=sender_wallet_id,
        receiver_wallet_id=receiver_wallet_id,
        amount=amount,
        status="flagged" if fraud_reason else "success",
    )
    db.add(new_transaction)

    try:
        db.flush()
        if fraud_reason:
            db.add(FraudFlag(transaction_id=new_transaction.id, reason=fraud_reason))

        # Two signed entries per movement. They always sum to zero.
        db.add(
            LedgerEntry(
                transaction_id=new_transaction.id,
                wallet_id=sender_wallet_id,
                amount=-amount,
                balance_after=sender_wallet.balance,
            )
        )
        db.add(
            LedgerEntry(
                transaction_id=new_transaction.id,
                wallet_id=receiver_wallet_id,
                amount=amount,
                balance_after=receiver_wallet.balance,
            )
        )
        db.commit()
    except IntegrityError:
        # Backstop: the unique constraint on idempotency_key is the final guard.
        db.rollback()
        existing = find_by_key(db, idempotency_key)
        if existing:
            return replay_if_same_request(
                existing, sender_wallet_id, receiver_wallet_id, amount
            )
        raise HTTPException(status_code=409, detail="Duplicate request")

    db.refresh(new_transaction)
    return new_transaction