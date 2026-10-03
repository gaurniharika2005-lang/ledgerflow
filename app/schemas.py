import uuid
from datetime import datetime
from pydantic import BaseModel, EmailStr
from decimal import Decimal


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

import uuid as uuid_module
from decimal import Decimal

class WalletOut(BaseModel):
    id: uuid.UUID
    balance: Decimal
    currency: str

    class Config:
        from_attributes = True