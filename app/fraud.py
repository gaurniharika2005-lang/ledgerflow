from datetime import datetime, timedelta, timezone
from sqlalchemy.orm import Session

from app.models import Transaction

LARGE_AMOUNT_THRESHOLD = 500
VELOCITY_WINDOW_MINUTES = 10
VELOCITY_MAX_TRANSACTIONS = 3


def check_fraud(db: Session, sender_wallet_id, amount) -> str | None:
    if amount > LARGE_AMOUNT_THRESHOLD:
        return f"Amount {amount} exceeds large-transaction threshold of {LARGE_AMOUNT_THRESHOLD}"

    window_start = datetime.now(timezone.utc) - timedelta(minutes=VELOCITY_WINDOW_MINUTES)
    recent_count = (
        db.query(Transaction)
        .filter(
            Transaction.sender_wallet_id == sender_wallet_id,
            Transaction.created_at >= window_start,
        )
        .count()
    )
    if recent_count >= VELOCITY_MAX_TRANSACTIONS:
        return f"More than {VELOCITY_MAX_TRANSACTIONS} transfers from this wallet in {VELOCITY_WINDOW_MINUTES} minutes"

    return None