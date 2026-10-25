import uuid
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import or_

from app.database import get_db
from app.dependencies import get_current_user
from app.ledger import move_money
from app.models import User, Wallet, Transaction
from app.schemas import TransferRequest, TransactionOut

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

    receiver_user = db.query(User).filter(User.email == transfer_in.receiver_email).first()
    if not receiver_user:
        raise HTTPException(status_code=404, detail="Receiver not found")

    if receiver_user.id == current_user.id:
        raise HTTPException(status_code=400, detail="Cannot transfer to yourself")

    # Column-only queries return plain values, so nothing stale is cached in the session.
    sender_wallet_id = db.query(Wallet.id).filter(Wallet.user_id == current_user.id).scalar()
    receiver_wallet_id = db.query(Wallet.id).filter(Wallet.user_id == receiver_user.id).scalar()
    if sender_wallet_id is None or receiver_wallet_id is None:
        raise HTTPException(status_code=404, detail="Wallet not found")

    return move_money(db, sender_wallet_id, receiver_wallet_id, transfer_in.amount, idempotency_key)


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