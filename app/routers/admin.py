import uuid
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import require_admin
from app.models import User, FraudFlag
from app.schemas import FraudFlagOut, FraudFlagReview

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