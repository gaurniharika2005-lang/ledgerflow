import uuid
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from sqlalchemy import or_

from app.database import get_db
from app.dependencies import get_current_user
from app.models import User, Wallet, Transaction, FraudFlag
from app.schemas import TransferRequest, TransactionOut
from app.fraud import check_fraud

router = APIRouter(prefix="/transactions", tags=["transactions"])


@router.post("/transfer", response_model=TransactionOut)
def transfer(
    transfer_in: TransferRequest,
    idempotency_key: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if transfer_in.amount <= 0:
        raise HTTPException(status_code=400, detail="Amount must be positive")

    existing = db.query(Transaction).filter(
        Transaction.idempotency_key == str(idempotency_key)
    ).first()
    if existing:
        return existing

    sender_wallet = db.query(Wallet).filter(Wallet.user_id == current_user.id).first()

    receiver_user = db.query(User).filter(User.email == transfer_in.receiver_email).first()
    if not receiver_user:
        raise HTTPException(status_code=404, detail="Receiver not found")

    if receiver_user.id == current_user.id:
        raise HTTPException(status_code=400, detail="Cannot transfer to yourself")

    receiver_wallet = db.query(Wallet).filter(Wallet.user_id == receiver_user.id).first()

    sender_wallet = (
        db.query(Wallet)
        .filter(Wallet.id == sender_wallet.id)
        .with_for_update()
        .first()
    )
    receiver_wallet = (
        db.query(Wallet)
        .filter(Wallet.id == receiver_wallet.id)
        .with_for_update()
        .first()
    )

    if sender_wallet.balance < transfer_in.amount:
        raise HTTPException(status_code=400, detail="Insufficient balance")

    fraud_reason = check_fraud(db, sender_wallet.id, transfer_in.amount)

    sender_wallet.balance -= transfer_in.amount
    sender_wallet.version += 1

    receiver_wallet.balance += transfer_in.amount
    receiver_wallet.version += 1

    new_transaction = Transaction(
        idempotency_key=str(idempotency_key),
        sender_wallet_id=sender_wallet.id,
        receiver_wallet_id=receiver_wallet.id,
        amount=transfer_in.amount,
        status="flagged" if fraud_reason else "success",
    )
    db.add(new_transaction)
    db.flush()

    if fraud_reason:
        flag = FraudFlag(transaction_id=new_transaction.id, reason=fraud_reason)
        db.add(flag)

    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Duplicate request")

    db.refresh(new_transaction)
    return new_transaction


@router.get("/history", response_model=List[TransactionOut])
def get_history(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    wallet = db.query(Wallet).filter(Wallet.user_id == current_user.id).first()

    transactions = (
        db.query(Transaction)
        .filter(
            or_(
                Transaction.sender_wallet_id == wallet.id,
                Transaction.receiver_wallet_id == wallet.id,
            )
        )
        .order_by(Transaction.created_at.desc())
        .all()
    )

    return transactions