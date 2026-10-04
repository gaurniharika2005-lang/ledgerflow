import uuid
from datetime import datetime
from decimal import Decimal
from pydantic import BaseModel, EmailStr


class UserCreate(BaseModel):
    email: EmailStr
    password: str
    full_name: str


class UserOut(BaseModel):
    id: uuid.UUID
    email: EmailStr
    full_name: str
    role: str
    is_active: bool
    created_at: datetime

    class Config:
        from_attributes = True


class WalletOut(BaseModel):
    id: uuid.UUID
    balance: Decimal
    currency: str

    class Config:
        from_attributes = True


class TransferRequest(BaseModel):
    receiver_email: EmailStr
    amount: Decimal


class TransactionOut(BaseModel):
    id: uuid.UUID
    sender_wallet_id: uuid.UUID
    receiver_wallet_id: uuid.UUID
    amount: Decimal
    currency: str
    status: str
    created_at: datetime

    class Config:
        from_attributes = True