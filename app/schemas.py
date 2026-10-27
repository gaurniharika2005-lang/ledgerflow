import uuid
from datetime import datetime
from decimal import Decimal
from typing import List, Literal, Optional

from pydantic import BaseModel, EmailStr, Field


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
    amount: Decimal = Field(gt=0, max_digits=18, decimal_places=2, allow_inf_nan=False)


class TransactionOut(BaseModel):
    id: uuid.UUID
    sender_wallet_id: uuid.UUID
    receiver_wallet_id: uuid.UUID
    amount: Decimal
    currency: str
    status: str
    created_at: datetime
    reverses_transaction_id: Optional[uuid.UUID] = None

    class Config:
        from_attributes = True
    


class FraudFlagOut(BaseModel):
    id: uuid.UUID
    transaction_id: uuid.UUID
    reason: str
    status: str
    created_at: datetime

    class Config:
        from_attributes = True


class FraudFlagReview(BaseModel):
    status: Literal["cleared", "confirmed"]


class CashRequest(BaseModel):
    user_email: EmailStr
    amount: Decimal = Field(gt=0, max_digits=18, decimal_places=2, allow_inf_nan=False)


class WalletMismatch(BaseModel):
    wallet_id: uuid.UUID
    balance: Decimal
    entries_sum: Decimal


class UnbalancedTransaction(BaseModel):
    transaction_id: uuid.UUID
    entries_sum: Decimal
    entry_count: int


class ReconciliationReport(BaseModel):
    balanced: bool
    total_of_balances: Decimal
    wallets_checked: int
    mismatched_wallets: List[WalletMismatch]
    unbalanced_transactions: List[UnbalancedTransaction]