import uuid
from concurrent.futures import ThreadPoolExecutor

from fastapi.testclient import TestClient

from app.models import User, Wallet

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


def set_balance(Session, email, amount):
    with Session() as session:
        user = session.query(User).filter(User.email == email).first()
        wallet = session.query(Wallet).filter(Wallet.user_id == user.id).first()
        wallet.balance = amount
        session.commit()


def get_balance(Session, email):
    with Session() as session:
        user = session.query(User).filter(User.email == email).first()
        wallet = session.query(Wallet).filter(Wallet.user_id == user.id).first()
        return wallet.balance


def send(app, token, receiver_email, amount):
    client = TestClient(app, raise_server_exceptions=False)
    return client.post(
        "/transactions/transfer",
        params={"idempotency_key": str(uuid.uuid4())},
        json={"receiver_email": receiver_email, "amount": amount},
        headers={"Authorization": f"Bearer {token}"},
    )


def run_parallel(jobs):
    with ThreadPoolExecutor(max_workers=10) as pool:
        futures = [pool.submit(*job) for job in jobs]
        return [future.result() for future in futures]


def test_parallel_transfers_never_overdraw(concurrent_env):
    app, Session = concurrent_env
    register(app, "sender@test.com")
    register(app, "receiver@test.com")
    set_balance(Session, "sender@test.com", 1000)
    token = login(app, "sender@test.com")

    responses = run_parallel(
        [(send, app, token, "receiver@test.com", 100) for _ in range(15)]
    )

    codes = [response.status_code for response in responses]
    assert codes.count(200) == 10
    assert codes.count(400) == 5
    assert get_balance(Session, "sender@test.com") == 0
    assert get_balance(Session, "receiver@test.com") == 1000


def test_opposite_direction_transfers_do_not_deadlock(concurrent_env):
    app, Session = concurrent_env
    register(app, "a@test.com")
    register(app, "b@test.com")
    set_balance(Session, "a@test.com", 1000)
    set_balance(Session, "b@test.com", 1000)
    token_a = login(app, "a@test.com")
    token_b = login(app, "b@test.com")

    jobs = []
    for _ in range(10):
        jobs.append((send, app, token_a, "b@test.com", 10))
        jobs.append((send, app, token_b, "a@test.com", 10))

    responses = run_parallel(jobs)

    codes = [response.status_code for response in responses]
    assert codes.count(200) == 20
    total = get_balance(Session, "a@test.com") + get_balance(Session, "b@test.com")
    assert total == 2000

def send_with_key(app, token, receiver_email, amount, key):
    client = TestClient(app, raise_server_exceptions=False)
    return client.post(
        "/transactions/transfer",
        params={"idempotency_key": key},
        json={"receiver_email": receiver_email, "amount": amount},
        headers={"Authorization": f"Bearer {token}"},
    )

def test_key_reuse_with_different_amount_is_rejected(concurrent_env):
    app, Session = concurrent_env
    register(app, "sender@test.com")
    register(app, "receiver@test.com")
    set_balance(Session, "sender@test.com", 1000)
    token = login(app, "sender@test.com")
    key = str(uuid.uuid4())

    first = send_with_key(app, token, "receiver@test.com", 100, key)
    second = send_with_key(app, token, "receiver@test.com", 200, key)

    assert first.status_code == 200
    assert second.status_code == 422
    assert get_balance(Session, "sender@test.com") == 900
    assert get_balance(Session, "receiver@test.com") == 100


def test_other_user_cannot_replay_someone_elses_key(concurrent_env):
    app, Session = concurrent_env
    register(app, "sender@test.com")
    register(app, "receiver@test.com")
    register(app, "other@test.com")
    set_balance(Session, "sender@test.com", 1000)
    set_balance(Session, "other@test.com", 1000)
    sender_token = login(app, "sender@test.com")
    other_token = login(app, "other@test.com")
    key = str(uuid.uuid4())

    first = send_with_key(app, sender_token, "receiver@test.com", 100, key)
    stolen = send_with_key(app, other_token, "receiver@test.com", 100, key)

    assert first.status_code == 200
    assert stolen.status_code == 422
    assert "id" not in stolen.json()
    assert get_balance(Session, "other@test.com") == 1000


def test_concurrent_duplicate_requests_charge_once(concurrent_env):
    app, Session = concurrent_env
    register(app, "sender@test.com")
    register(app, "receiver@test.com")
    set_balance(Session, "sender@test.com", 1000)
    token = login(app, "sender@test.com")
    key = str(uuid.uuid4())

    responses = run_parallel(
        [(send_with_key, app, token, "receiver@test.com", 100, key) for _ in range(10)]
    )

    codes = sorted(response.status_code for response in responses)
    assert codes == [200] * 10, codes
    assert len({response.json()["id"] for response in responses}) == 1
    assert get_balance(Session, "sender@test.com") == 900
    assert get_balance(Session, "receiver@test.com") == 100

def test_amount_with_extra_decimals_cannot_create_money(concurrent_env):
    app, Session = concurrent_env
    register(app, "sender@test.com")
    register(app, "receiver@test.com")
    set_balance(Session, "sender@test.com", 1000)
    token = login(app, "sender@test.com")

    for _ in range(20):
        send_with_key(app, token, "receiver@test.com", "0.005", str(uuid.uuid4()))

    total = get_balance(Session, "sender@test.com") + get_balance(Session, "receiver@test.com")
    assert total == 1000

def get_entries(Session, email):
    from app.models import LedgerEntry

    with Session() as session:
        user = session.query(User).filter(User.email == email).first()
        wallet = session.query(Wallet).filter(Wallet.user_id == user.id).first()
        rows = session.query(LedgerEntry).filter(LedgerEntry.wallet_id == wallet.id).all()
        return [(row.amount, row.balance_after) for row in rows]


def test_parallel_transfers_leave_a_consistent_ledger(concurrent_env):
    app, Session = concurrent_env
    register(app, "sender@test.com")
    register(app, "receiver@test.com")
    set_balance(Session, "sender@test.com", 1000)
    token = login(app, "sender@test.com")

    run_parallel([(send, app, token, "receiver@test.com", 100) for _ in range(15)])

    sender_rows = get_entries(Session, "sender@test.com")
    receiver_rows = get_entries(Session, "receiver@test.com")

    # Only the 10 successful transfers wrote entries; the 5 rejected ones wrote nothing.
    assert len(sender_rows) == 10
    assert len(receiver_rows) == 10
    assert sum(amount for amount, _ in sender_rows) == -1000
    assert sum(amount for amount, _ in receiver_rows) == 1000

    # Transfers are serialized by the row lock, so each balance_after appears exactly once.
    assert sorted(after for _, after in sender_rows) == [i * 100 for i in range(10)]
    assert sorted(after for _, after in receiver_rows) == [i * 100 for i in range(1, 11)]