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


def call(app, token, path, body=None, key=None):
    client = TestClient(app, raise_server_exceptions=False)
    return client.post(
        path,
        params={"idempotency_key": str(key or uuid.uuid4())},
        json=body,
        headers={"Authorization": f"Bearer {token}"},
    )


def reverse(app, token, transaction_id, key=None):
    return call(app, token, f"/admin/transactions/{transaction_id}/reverse", key=key)


def send(app, token, receiver_email, amount):
    return call(app, token, "/transactions/transfer", {"receiver_email": receiver_email, "amount": amount})


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


def entries_for(Session, transaction_id):
    with Session() as session:
        rows = (
            session.query(LedgerEntry.amount)
            .filter(LedgerEntry.transaction_id == uuid.UUID(transaction_id))
            .all()
        )
        return sorted(amount for (amount,) in rows)


def run_parallel(jobs):
    with ThreadPoolExecutor(max_workers=10) as pool:
        futures = [pool.submit(*job) for job in jobs]
        return [future.result() for future in futures]


def setup(app, Session):
    """An admin, alice (deposited 500) and bob. Returns (admin_token, alice_token, deposit_id)."""
    register(app, "admin@test.com")
    make_admin(Session, "admin@test.com")
    admin_token = login(app, "admin@test.com")
    register(app, "alice@test.com")
    register(app, "bob@test.com")
    deposit = call(app, admin_token, "/admin/deposits", {"user_email": "alice@test.com", "amount": 500})
    assert deposit.status_code == 200
    return admin_token, login(app, "alice@test.com"), deposit.json()["id"]


def test_reversing_a_transfer_restores_balances(concurrent_env):
    app, Session = concurrent_env
    admin_token, alice_token, _ = setup(app, Session)
    sent = send(app, alice_token, "bob@test.com", 120)
    assert sent.status_code == 200
    original_id = sent.json()["id"]

    response = reverse(app, admin_token, original_id)

    assert response.status_code == 200
    body = response.json()
    assert body["reverses_transaction_id"] == original_id
    assert body["amount"] == "120.00"
    assert balance_of(Session, "alice@test.com") == 500
    assert balance_of(Session, "bob@test.com") == 0
    assert total_of_all_balances(Session) == 0
    assert wallets_that_disagree_with_their_entries(Session) == []
    # History is only ever added to: the original entries are untouched.
    assert entries_for(Session, original_id) == [-120, 120]
    assert entries_for(Session, body["id"]) == [-120, 120]


def test_reversing_a_deposit_returns_money_to_the_system_account(concurrent_env):
    app, Session = concurrent_env
    admin_token, _, deposit_id = setup(app, Session)
    assert system_balance(Session) == -500

    response = reverse(app, admin_token, deposit_id)

    assert response.status_code == 200
    assert balance_of(Session, "alice@test.com") == 0
    assert system_balance(Session) == 0
    assert total_of_all_balances(Session) == 0


def test_retrying_the_same_reversal_returns_the_same_transaction(concurrent_env):
    app, Session = concurrent_env
    admin_token, alice_token, _ = setup(app, Session)
    original_id = send(app, alice_token, "bob@test.com", 120).json()["id"]
    key = uuid.uuid4()

    first = reverse(app, admin_token, original_id, key=key)
    second = reverse(app, admin_token, original_id, key=key)

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["id"] == second.json()["id"]
    assert balance_of(Session, "alice@test.com") == 500
    assert balance_of(Session, "bob@test.com") == 0


def test_a_transaction_cannot_be_reversed_twice(concurrent_env):
    app, Session = concurrent_env
    admin_token, alice_token, _ = setup(app, Session)
    original_id = send(app, alice_token, "bob@test.com", 120).json()["id"]

    assert reverse(app, admin_token, original_id).status_code == 200
    second = reverse(app, admin_token, original_id)

    assert second.status_code == 409
    assert second.json()["detail"] == "Transaction has already been reversed"
    assert balance_of(Session, "alice@test.com") == 500
    assert total_of_all_balances(Session) == 0


def test_concurrent_reversals_apply_exactly_once(concurrent_env):
    app, Session = concurrent_env
    admin_token, alice_token, _ = setup(app, Session)
    original_id = send(app, alice_token, "bob@test.com", 120).json()["id"]

    responses = run_parallel([(reverse, app, admin_token, original_id) for _ in range(10)])

    codes = sorted(response.status_code for response in responses)
    assert codes == [200] + [409] * 9, codes
    assert balance_of(Session, "alice@test.com") == 500
    assert balance_of(Session, "bob@test.com") == 0
    assert total_of_all_balances(Session) == 0
    assert wallets_that_disagree_with_their_entries(Session) == []


def test_cannot_reverse_when_the_receiver_already_spent_the_money(concurrent_env):
    app, Session = concurrent_env
    admin_token, alice_token, _ = setup(app, Session)
    original_id = send(app, alice_token, "bob@test.com", 120).json()["id"]
    cash_out = call(app, admin_token, "/admin/withdrawals", {"user_email": "bob@test.com", "amount": 100})
    assert cash_out.status_code == 200

    response = reverse(app, admin_token, original_id)

    assert response.status_code == 400
    assert "no longer has enough funds" in response.json()["detail"]
    assert balance_of(Session, "alice@test.com") == 380
    assert balance_of(Session, "bob@test.com") == 20
    assert total_of_all_balances(Session) == 0


def test_a_reversal_cannot_itself_be_reversed(concurrent_env):
    app, Session = concurrent_env
    admin_token, alice_token, _ = setup(app, Session)
    original_id = send(app, alice_token, "bob@test.com", 120).json()["id"]
    reversal_id = reverse(app, admin_token, original_id).json()["id"]

    response = reverse(app, admin_token, reversal_id)

    assert response.status_code == 400
    assert response.json()["detail"] == "A reversal cannot itself be reversed"
    assert balance_of(Session, "alice@test.com") == 500


def test_non_admin_is_forbidden_and_unknown_transaction_is_not_found(concurrent_env):
    app, Session = concurrent_env
    admin_token, alice_token, _ = setup(app, Session)
    original_id = send(app, alice_token, "bob@test.com", 120).json()["id"]

    assert reverse(app, alice_token, original_id).status_code == 403
    assert reverse(app, admin_token, uuid.uuid4()).status_code == 404
    assert balance_of(Session, "alice@test.com") == 380
    assert balance_of(Session, "bob@test.com") == 120