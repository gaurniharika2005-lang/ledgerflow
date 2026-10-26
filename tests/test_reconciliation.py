import uuid
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

from fastapi.testclient import TestClient

from app.models import LedgerEntry, Transaction, User, Wallet

PASSWORD = "testpass123"


def register(app, email):
    client = TestClient(app)
    response = client.post(
        "/auth/register",
        json={"email": email, "password": PASSWORD, "full_name": "Test User"},
    )
    assert response.status_code == 200


def login(app, email):
    client = TestClient(app)
    response = client.post("/auth/login", data={"username": email, "password": PASSWORD})
    assert response.status_code == 200
    return response.json()["access_token"]


def make_admin(Session, email):
    with Session() as session:
        user = session.query(User).filter(User.email == email).first()
        user.role = "admin"
        session.commit()


def post(app, token, path, body):
    client = TestClient(app, raise_server_exceptions=False)
    return client.post(
        path,
        params={"idempotency_key": str(uuid.uuid4())},
        json=body,
        headers={"Authorization": f"Bearer {token}"},
    )


def transfer(app, token, receiver_email, amount):
    return post(app, token, "/transactions/transfer", {"receiver_email": receiver_email, "amount": amount})


def get_report(app, token):
    client = TestClient(app, raise_server_exceptions=False)
    return client.get("/admin/reconciliation", headers={"Authorization": f"Bearer {token}"})


def run_parallel(jobs):
    with ThreadPoolExecutor(max_workers=10) as pool:
        futures = [pool.submit(*job) for job in jobs]
        return [future.result() for future in futures]


def build_activity(app, Session):
    """An admin, two users, and some deposits, a transfer, and a withdrawal."""
    register(app, "admin@test.com")
    make_admin(Session, "admin@test.com")
    admin_token = login(app, "admin@test.com")
    register(app, "alice@test.com")
    register(app, "bob@test.com")

    assert post(app, admin_token, "/admin/deposits", {"user_email": "alice@test.com", "amount": 500}).status_code == 200
    assert post(app, admin_token, "/admin/deposits", {"user_email": "bob@test.com", "amount": 200}).status_code == 200
    alice_token = login(app, "alice@test.com")
    assert transfer(app, alice_token, "bob@test.com", 120).status_code == 200
    assert post(app, admin_token, "/admin/withdrawals", {"user_email": "bob@test.com", "amount": 50}).status_code == 200
    return admin_token


def test_clean_ledger_reconciles(concurrent_env):
    app, Session = concurrent_env
    admin_token = build_activity(app, Session)

    response = get_report(app, admin_token)

    assert response.status_code == 200
    report = response.json()
    assert report["balanced"] is True
    assert Decimal(report["total_of_balances"]) == 0
    assert report["wallets_checked"] == 4  # admin, alice, bob, and the system wallet
    assert report["mismatched_wallets"] == []
    assert report["unbalanced_transactions"] == []


def test_tampered_balance_is_detected(concurrent_env):
    app, Session = concurrent_env
    admin_token = build_activity(app, Session)

    with Session() as session:
        wallet = (
            session.query(Wallet)
            .join(User, User.id == Wallet.user_id)
            .filter(User.email == "alice@test.com")
            .first()
        )
        wallet_id = wallet.id
        wallet.balance = wallet.balance + 50  # money edited without a ledger entry
        session.commit()

    report = get_report(app, admin_token).json()

    assert report["balanced"] is False
    assert Decimal(report["total_of_balances"]) == 50
    assert len(report["mismatched_wallets"]) == 1
    mismatch = report["mismatched_wallets"][0]
    assert mismatch["wallet_id"] == str(wallet_id)
    assert Decimal(mismatch["balance"]) - Decimal(mismatch["entries_sum"]) == 50


def test_unbalanced_transaction_is_detected(concurrent_env):
    app, Session = concurrent_env
    admin_token = build_activity(app, Session)

    with Session() as session:
        transaction = session.query(Transaction).first()
        wallet = session.query(Wallet).filter(Wallet.is_system.is_(False)).first()
        transaction_id = transaction.id
        session.add(
            LedgerEntry(
                transaction_id=transaction.id,
                wallet_id=wallet.id,
                amount=10,
                balance_after=0,
            )
        )
        session.commit()

    report = get_report(app, admin_token).json()

    assert report["balanced"] is False
    assert [item["transaction_id"] for item in report["unbalanced_transactions"]] == [str(transaction_id)]


def test_non_admin_cannot_view_reconciliation(concurrent_env):
    app, Session = concurrent_env
    register(app, "user@test.com")
    token = login(app, "user@test.com")

    assert get_report(app, token).status_code == 403


def test_reconciliation_has_no_false_alarms_during_live_traffic(concurrent_env):
    app, Session = concurrent_env
    admin_token = build_activity(app, Session)
    alice_token = login(app, "alice@test.com")
    bob_token = login(app, "bob@test.com")

    jobs = [(transfer, app, alice_token, "bob@test.com", 10) for _ in range(10)]
    jobs += [(transfer, app, bob_token, "alice@test.com", 5) for _ in range(10)]
    jobs += [(get_report, app, admin_token) for _ in range(20)]
    responses = run_parallel(jobs)

    assert [response.status_code for response in responses] == [200] * 40
    reports = [response.json() for response in responses[20:]]
    assert all(report["balanced"] is True for report in reports)