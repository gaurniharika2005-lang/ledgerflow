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