import uuid
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import require_admin
from app.ledger import get_system_wallet_id, move_money
from app.models import FraudFlag, Transaction, User, Wallet
from app.reconciliation import reconcile
from app.schemas import (
    CashRequest,
    FraudFlagOut,
    FraudFlagReview,
    ReconciliationReport,
    TransactionOut,
)

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/flags", response_model=List[FraudFlagOut])
def list_open_flags(admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    return (
        db.query(FraudFlag)
        .filter(FraudFlag.status == "open")
        .order_by(FraudFlag.created_at.desc())
        .all()
    )


@router.post("/flags/{flag_id}/review", response_model=FraudFlagOut)
def review_flag(
    flag_id: uuid.UUID,
    review: FraudFlagReview,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    flag = db.query(FraudFlag).filter(FraudFlag.id == flag_id).first()
    if not flag:
        raise HTTPException(status_code=404, detail="Flag not found")

    flag.status = review.status
    db.commit()
    db.refresh(flag)
    return flag


def user_wallet_id_by_email(db: Session, email: str) -> uuid.UUID:
    wallet_id = (
        db.query(Wallet.id)
        .join(User, User.id == Wallet.user_id)
        .filter(User.email == email, Wallet.is_system.is_(False))
        .scalar()
    )
    if wallet_id is None:
        raise HTTPException(status_code=404, detail="User not found")
    return wallet_id


@router.post("/deposits", response_model=TransactionOut)
def deposit(
    cash_in: CashRequest,
    idempotency_key: uuid.UUID,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    user_wallet_id = user_wallet_id_by_email(db, cash_in.user_email)
    system_wallet_id = get_system_wallet_id(db)
    return move_money(db, system_wallet_id, user_wallet_id, cash_in.amount, idempotency_key)


@router.post("/withdrawals", response_model=TransactionOut)
def withdraw(
    cash_out: CashRequest,
    idempotency_key: uuid.UUID,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    user_wallet_id = user_wallet_id_by_email(db, cash_out.user_email)
    system_wallet_id = get_system_wallet_id(db)
    return move_money(db, user_wallet_id, system_wallet_id, cash_out.amount, idempotency_key)


@router.post("/transactions/{transaction_id}/reverse", response_model=TransactionOut)
def reverse_transaction(
    transaction_id: uuid.UUID,
    idempotency_key: uuid.UUID,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    original = db.query(Transaction).filter(Transaction.id == transaction_id).first()
    if original is None:
        raise HTTPException(status_code=404, detail="Transaction not found")
    if original.reverses_transaction_id is not None:
        raise HTTPException(status_code=400, detail="A reversal cannot itself be reversed")

    # The money goes back the way it came: receiver pays, sender is repaid.
    return move_money(
        db,
        original.receiver_wallet_id,
        original.sender_wallet_id,
        original.amount,
        idempotency_key,
        reverses_transaction_id=original.id,
    )


@router.get("/reconciliation", response_model=ReconciliationReport)
def reconciliation(admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    return reconcile(db)