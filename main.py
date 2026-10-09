from fastapi import FastAPI

from app.routers import auth, wallets, transactions, admin

app = FastAPI(title="LedgerFlow API")

app.include_router(auth.router)
app.include_router(wallets.router)
app.include_router(transactions.router)
app.include_router(admin.router)


@app.get("/")
def read_root():
    return {"message": "LedgerFlow is alive"}