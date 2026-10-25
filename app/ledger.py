import secrets
import uuid
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.fraud import check_fraud
from app.models import FraudFlag, LedgerEntry, Transaction, User, Wallet
from app.security import hash_password

# Not a valid email address, so the API (which validates emails) can never
# register this account or name it as a transfer recipient.
SYSTEM_EMAIL = "system"


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


def get_system_wallet_id(db: Session) -> uuid.UUID:
    """Return the id of the system wallet, creating it on first use.

    If several requests race to create it, the unique constraint on users.email
    lets exactly one win; the others roll back and read the winner's wallet.
    """
    wallet_id = db.query(Wallet.id).filter(Wallet.is_system.is_(True)).scalar()
    if wallet_id is not None:
        return wallet_id

    try:
        system_user = User(
            email=SYSTEM_EMAIL,
            password_hash=hash_password(secrets.token_hex(32)),  # random, never revealed
            full_name="LedgerFlow System",
            role="system",
            is_active=False,
        )
        db.add(system_user)
        db.flush()
        system_wallet = Wallet(user_id=system_user.id, is_system=True)
        db.add(system_wallet)
        db.flush()
        wallet_id = system_wallet.id
        db.commit()
        return wallet_id
    except IntegrityError:
        db.rollback()
        wallet_id = db.query(Wallet.id).filter(Wallet.is_system.is_(True)).scalar()
        if wallet_id is None:
            raise
        return wallet_id


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

    # Only the system wallet may go negative (it represents money held outside).
    if not sender_wallet.is_system and sender_wallet.balance < amount:
        raise HTTPException(status_code=400, detail="Insufficient balance")

    fraud_reason = None
    if not sender_wallet.is_system:
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