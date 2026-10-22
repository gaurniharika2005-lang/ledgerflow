from fastapi import Depends, FastAPI, HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.database import get_db
from app.routers import auth, wallets, transactions, admin

app = FastAPI(title="LedgerFlow API")

app.include_router(auth.router)
app.include_router(wallets.router)
app.include_router(transactions.router)
app.include_router(admin.router)


@app.api_route("/", methods=["GET", "HEAD"])
def read_root():
    return {"message": "LedgerFlow is alive"}


@app.api_route("/health", methods=["GET", "HEAD"])
def health():
    return {"status": "ok"}


@app.get("/health/db")
def health_db(db: Session = Depends(get_db)):
    try:
        db.execute(text("SELECT 1"))
    except Exception:
        raise HTTPException(status_code=503, detail="Database unreachable")
    return {"status": "ok", "database": "reachable"}