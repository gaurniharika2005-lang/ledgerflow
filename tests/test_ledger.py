import uuid
from concurrent.futures import ThreadPoolExecutor

from fastapi.testclient import TestClient
from sqlalchemy import func

from app.models import LedgerEntry, User, Wallet

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


def post(app, token, path, body, key=None):
    client = TestClient(app, raise_server_exceptions=False)
    return client.post(
        path,
        params={"idempotency_key": str(key or uuid.uuid4())},
        json=body,
        headers={"Authorization": f"Bearer {token}"},
    )


def deposit(app, token, email, amount):
    return post(app, token, "/admin/deposits", {"user_email": email, "amount": amount})


def withdraw(app, token, email, amount):
    return post(app, token, "/admin/withdrawals", {"user_email": email, "amount": amount})


def transfer(app, token, receiver_email, amount):
    return post(app, token, "/transactions/transfer", {"receiver_email": receiver_email, "amount": amount})


def balance_of(Session, email):
    with Session() as session:
        return (
            session.query(Wallet.balance)
            .join(User, User.id == Wallet.user_id)
            .filter(User.email == email)
            .scalar()
        )


def system_balance(Session):
    with Session() as session:
        return session.query(Wallet.balance).filter(Wallet.is_system.is_(True)).scalar()


def system_wallet_count(Session):
    with Session() as session:
        return session.query(Wallet).filter(Wallet.is_system.is_(True)).count()


def total_of_all_balances(Session):
    with Session() as session:
        return session.query(func.coalesce(func.sum(Wallet.balance), 0)).scalar()


def wallets_that_disagree_with_their_entries(Session):
    with Session() as session:
        rows = (
            session.query(Wallet.id, Wallet.balance, func.coalesce(func.sum(LedgerEntry.amount), 0))
            .outerjoin(LedgerEntry, LedgerEntry.wallet_id == Wallet.id)
            .group_by(Wallet.id, Wallet.balance)
            .all()
        )
        return [wallet_id for wallet_id, balance, entry_sum in rows if balance != entry_sum]


def run_parallel(jobs):
    with ThreadPoolExecutor(max_workers=10) as pool:
        futures = [pool.submit(*job) for job in jobs]
        return [future.result() for future in futures]


def setup_admin(app, Session):
    register(app, "admin@test.com")
    make_admin(Session, "admin@test.com")
    return login(app, "admin@test.com")


def test_deposit_credits_wallet_and_system_account_goes_negative(concurrent_env):
    app, Session = concurrent_env
    admin_token = setup_admin(app, Session)
    register(app, "user@test.com")

    response = deposit(app, admin_token, "user@test.com", 500)

    assert response.status_code == 200
    assert balance_of(Session, "user@test.com") == 500
    assert system_balance(Session) == -500
    assert total_of_all_balances(Session) == 0
    assert wallets_that_disagree_with_their_entries(Session) == []


def test_non_admin_cannot_deposit_or_withdraw(concurrent_env):
    app, Session = concurrent_env
    register(app, "user@test.com")
    token = login(app, "user@test.com")

    assert deposit(app, token, "user@test.com", 500).status_code == 403
    assert withdraw(app, token, "user@test.com", 100).status_code == 403
    assert balance_of(Session, "user@test.com") == 0
    assert system_wallet_count(Session) == 0


def test_withdrawal_reduces_balance_and_cannot_overdraw(concurrent_env):
    app, Session = concurrent_env
    admin_token = setup_admin(app, Session)
    register(app, "user@test.com")

    assert deposit(app, admin_token, "user@test.com", 300).status_code == 200
    assert withdraw(app, admin_token, "user@test.com", 100).status_code == 200
    assert balance_of(Session, "user@test.com") == 200
    assert system_balance(Session) == -200

    too_much = withdraw(app, admin_token, "user@test.com", 500)
    assert too_much.status_code == 400
    assert balance_of(Session, "user@test.com") == 200
    assert total_of_all_balances(Session) == 0


def test_total_money_stays_zero_under_concurrent_activity(concurrent_env):
    app, Session = concurrent_env
    admin_token = setup_admin(app, Session)
    register(app, "alice@test.com")
    register(app, "bob@test.com")
    alice_token = login(app, "alice@test.com")
    bob_token = login(app, "bob@test.com")

    # The first deposits race to create the system account: exactly one must be made.
    jobs = [(deposit, app, admin_token, "alice@test.com", 100) for _ in range(5)]
    jobs += [(deposit, app, admin_token, "bob@test.com", 100) for _ in range(5)]
    responses = run_parallel(jobs)
    assert [r.status_code for r in responses] == [200] * 10
    assert system_wallet_count(Session) == 1

    # Transfers in opposite directions, in parallel.
    jobs = [(transfer, app, alice_token, "bob@test.com", 50) for _ in range(6)]
    jobs += [(transfer, app, bob_token, "alice@test.com", 30) for _ in range(6)]
    responses = run_parallel(jobs)
    assert [r.status_code for r in responses] == [200] * 12

    responses = run_parallel(
        [
            (withdraw, app, admin_token, "alice@test.com", 100),
            (withdraw, app, admin_token, "bob@test.com", 100),
        ]
    )
    assert [r.status_code for r in responses] == [200] * 2

    assert total_of_all_balances(Session) == 0
    assert system_balance(Session) == -800
    assert wallets_that_disagree_with_their_entries(Session) == []


def test_system_account_cannot_be_registered_or_targeted(concurrent_env):
    app, Session = concurrent_env
    register(app, "user@test.com")
    token = login(app, "user@test.com")

    client = TestClient(app, raise_server_exceptions=False)
    registered = client.post(
        "/auth/register",
        json={"email": "system", "password": PASSWORD, "full_name": "Impostor"},
    )
    assert registered.status_code == 422
    assert transfer(app, token, "system", 10).status_code == 422