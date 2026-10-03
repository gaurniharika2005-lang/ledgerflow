from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_current_user
from app.models import User, Wallet
from app.schemas import WalletOut

router = APIRouter(prefix="/wallets", tags=["wallets"])


@router.get("/me", response_model=WalletOut)
def get_my_wallet(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    wallet = db.query(Wallet).filter(Wallet.user_id == current_user.id).first()
    return wallet