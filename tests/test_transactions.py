def register_user(client, email, password="testpass123", full_name="Test User"):
    response = client.post(
        "/auth/register",
        json={"email": email, "password": password, "full_name": full_name},
    )
    assert response.status_code == 200
    return response.json()


def login_user(client, email, password="testpass123"):
    response = client.post(
        "/auth/login",
        data={"username": email, "password": password},
    )
    assert response.status_code == 200
    return response.json()["access_token"]


def test_successful_transfer(client, db_session):
    register_user(client, "sender@test.com")
    register_user(client, "receiver@test.com")

    from app.models import Wallet, User
    sender_user = db_session.query(User).filter(User.email == "sender@test.com").first()
    sender_wallet = db_session.query(Wallet).filter(Wallet.user_id == sender_user.id).first()
    sender_wallet.balance = 1000
    db_session.commit()

    token = login_user(client, "sender@test.com")
    headers = {"Authorization": f"Bearer {token}"}

    response = client.post(
        "/transactions/transfer",
        params={"idempotency_key": "11111111-1111-1111-1111-111111111111"},
        json={"receiver_email": "receiver@test.com", "amount": 300},
        headers=headers,
    )

    assert response.status_code == 200
    data = response.json()
    assert data["amount"] == "300.00"
    assert data["status"] == "success"


def test_idempotent_transfer_does_not_double_charge(client, db_session):
    register_user(client, "sender2@test.com")
    register_user(client, "receiver2@test.com")

    from app.models import Wallet, User
    sender_user = db_session.query(User).filter(User.email == "sender2@test.com").first()
    sender_wallet = db_session.query(Wallet).filter(Wallet.user_id == sender_user.id).first()
    sender_wallet.balance = 1000
    db_session.commit()

    token = login_user(client, "sender2@test.com")
    headers = {"Authorization": f"Bearer {token}"}
    idempotency_key = "22222222-2222-2222-2222-222222222222"

    first_response = client.post(
        "/transactions/transfer",
        params={"idempotency_key": idempotency_key},
        json={"receiver_email": "receiver2@test.com", "amount": 300},
        headers=headers,
    )
    assert first_response.status_code == 200
    first_id = first_response.json()["id"]

    second_response = client.post(
        "/transactions/transfer",
        params={"idempotency_key": idempotency_key},
        json={"receiver_email": "receiver2@test.com", "amount": 300},
        headers=headers,
    )
    assert second_response.status_code == 200
    second_id = second_response.json()["id"]

    assert first_id == second_id

    db_session.refresh(sender_wallet)
    assert sender_wallet.balance == 700


def test_insufficient_balance_rejected(client, db_session):
    register_user(client, "poor@test.com")
    register_user(client, "receiver3@test.com")

    token = login_user(client, "poor@test.com")
    headers = {"Authorization": f"Bearer {token}"}

    response = client.post(
        "/transactions/transfer",
        params={"idempotency_key": "33333333-3333-3333-3333-333333333333"},
        json={"receiver_email": "receiver3@test.com", "amount": 500},
        headers=headers,
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Insufficient balance"