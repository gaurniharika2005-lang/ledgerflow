# LedgerFlow

![CI](https://github.com/gaurniharika2005-lang/ledgerflow/actions/workflows/ci.yml/badge.svg)

A digital wallet and payment-processing backend. Users register, get a wallet automatically, and transfer money to each other. Transfers are atomic, safe to retry, safe under concurrent load, and screened by a rule-based fraud check, with an admin workflow for reviewing flagged transactions.

**Live API:** https://ledgerflow-api-yio7.onrender.com/docs (interactive Swagger UI)

> The live API runs on free tiers (Render and Neon) that sleep when idle, so the first request after a quiet period can take several seconds.

## Why this project

Payment systems have a few problems that ordinary CRUD apps don't. LedgerFlow is built around these:

- **A retried request must never charge twice.** Networks fail and clients retry, so every transfer carries an idempotency key.
- **Two requests touching the same balance must not corrupt it.** Wallet rows are locked, in a fixed order, while a transfer runs.
- **Money amounts must be exact.** Amounts are validated at the API boundary so the database never has to round.
- **Suspicious activity needs a paper trail and a human decision.** Transfers that trip a rule are flagged with a reason and queued for admin review.

Each guarantee above has an automated test that attacks it (see [What the tests prove](#what-the-tests-prove)).

## Features

- **Authentication:** registration, login, JWT access tokens (15-minute expiry), protected routes, role-based access (user / admin)
- **Wallets:** created automatically at registration; balances stored as exact decimals
- **Transfers:** `POST /transactions/transfer` is idempotent, row-locked, and atomic
- **History:** a user's sent and received transactions, newest first
- **Fraud rules:** a transfer is flagged if the amount exceeds a threshold or the sender makes too many transfers in a short window. A flagged transfer still completes, and is recorded with the reason for review
- **Admin review:** list open flags and mark each as cleared or confirmed
- **Health checks:** `/health` (is the process up?) and `/health/db` (can it reach the database?)
- **Tests and CI:** 14 automated tests, including concurrency tests, run against a real PostgreSQL instance on every push

## Architecture

```
Client (Swagger UI or any HTTP client)
        |  HTTPS
        v
FastAPI app  (Docker container on Render, Virginia)
  routers:       auth | wallets | transactions | admin
  dependencies:  get_current_user -> require_admin
  logic:         fraud rules | bcrypt password hashing | JWT
        |  SQLAlchemy 2.0 (psycopg2)
        v
PostgreSQL 16  (Neon, us-east-1)
  users | wallets | transactions | fraud_flags
  schema managed with Alembic migrations
```

**CI/CD:** a push to `main` runs the tests in GitHub Actions (with a Postgres service container) and triggers an automatic deploy on Render.

## Tech stack

| Area | Tools |
|---|---|
| API | Python, FastAPI, Pydantic v2, Uvicorn |
| Database | PostgreSQL 16, SQLAlchemy 2.0, Alembic |
| Security | bcrypt (passlib), JWT (python-jose) |
| Testing | pytest, FastAPI TestClient, ThreadPoolExecutor for concurrency tests, GitHub Actions |
| Deployment | Docker, Render, Neon |

## API

| Method | Path | Access | Description |
|---|---|---|---|
| POST | `/auth/register` | public | Create a user and their wallet |
| POST | `/auth/login` | public | Log in with email and password, receive a JWT |
| GET | `/auth/me` | user | Current user's profile |
| GET | `/wallets/me` | user | Current user's wallet and balance |
| POST | `/transactions/transfer?idempotency_key=<uuid>` | user | Transfer money to another user by email |
| GET | `/transactions/history` | user | Sent and received transactions |
| GET | `/admin/flags` | admin | List open fraud flags |
| POST | `/admin/flags/{flag_id}/review` | admin | Mark a flag `cleared` or `confirmed` |
| GET | `/health` | public | Liveness check |
| GET | `/health/db` | public | Readiness check (runs `SELECT 1`) |

Full request and response schemas are in the interactive docs at `/docs`.

## Design decisions

1. **Money is `NUMERIC(18,2)`, never a float.** Floating-point numbers can't represent most decimal amounts exactly.
2. **Amounts are validated at the API boundary.** A transfer amount must be greater than zero, finite, at most 18 digits, and have at most 2 decimal places. Invalid amounts get `422`. Without this, an amount like `0.005` is accepted and the database rounds the sender's and receiver's balances in different directions, creating money (see the bugs below).
3. **Idempotency by a unique constraint plus a check under lock.** The client sends a UUID with each transfer, and `transactions.idempotency_key` is `UNIQUE`. The key is checked *after* the wallet locks are held, so a racing duplicate sees the winner's committed transaction and returns it instead of processing again. If an insert still hits the unique constraint, the handler rolls back and returns the original transaction.
4. **A key can only replay the request it was first used for.** Reusing a key with a different sender, receiver, or amount returns `422`, so a client bug can't silently receive the wrong transaction and another user can't read someone else's.
5. **Row-level locking in a fixed order.** Both wallets are loaded in a single `SELECT ... FOR UPDATE` query ordered by wallet id, so two transfers never take locks in opposite orders (no deadlocks). The rows are loaded with `populate_existing()` so the balance checked is the freshly locked value, not a copy cached earlier in the session.
6. **Atomic writes.** The debit, the credit, the transaction record, and any fraud flag commit together or not at all.
7. **UUID primary keys.** Sequential IDs leak information, such as how many users or transactions exist.
8. **Flag and review, don't block.** Simple fixed thresholds produce false positives, so a flagged transfer completes and a person decides.
9. **Admin checks hit the database.** `require_admin` reads the role from the database, not from the token, so a demoted admin loses access immediately. Non-admins get `403`, not `401`, because they are authenticated but not permitted.
10. **Login errors are generic.** "Incorrect email or password" is returned whether the email or the password is wrong, so the API doesn't reveal which emails are registered.
11. **Response models exclude secrets.** The password hash is not a field on any response schema.
12. **Two health endpoints.** `/health` doesn't touch the database, so a platform polling it won't keep the database awake. `/health/db` is for diagnosing connectivity.
13. **Isolated tests.** Tests run against a separate PostgreSQL database, with tables created and dropped around each test. FastAPI's dependency override swaps in the test session.
14. **Configuration from the environment.** Secrets are never in the repository or the Docker image (`.env` is git-ignored and docker-ignored).

## What the tests prove

- **No overdraft under load:** 15 parallel transfers of ₹100 from a ₹1000 wallet result in exactly 10 successes, 5 rejections, a sender balance of 0, and a receiver balance of ₹1000.
- **No deadlocks:** 20 simultaneous transfers in opposite directions between two wallets all succeed, and the total money is unchanged.
- **Idempotency under a race:** 10 simultaneous requests with the same key return the same transaction and charge exactly once.
- **Key misuse is rejected:** replaying a key with a different amount, or from a different user, returns `422` and moves no money.
- **Amounts can't create money:** invalid amounts (`0.005`, `0`, `-5`, `NaN`, `Infinity`, `1e30`, `abc`) are rejected with `422` and leave balances unchanged, and the earlier rounding bug no longer reproduces.
- **Basics:** successful transfer, insufficient balance, and the health endpoints.

The concurrency tests are timing-based, so after changing transfer code I run them repeatedly (the current version passed 40 consecutive runs).

### Bugs the tests found in this code

These were real bugs in an earlier version of the transfer endpoint, found by writing tests designed to break it:

1. **Lost updates:** 15 of 15 parallel transfers succeeded when only 10 should have. The locked wallet was reloaded, but the session kept using a stale cached copy of the balance. Fixed by reading the locked rows with `populate_existing()`.
2. **Deadlocks:** opposite-direction transfers locked wallets in opposite orders, and PostgreSQL aborted most of them (12 logged `deadlock detected` errors in one run). Fixed with a single ordered locking query.
3. **Idempotency race:** simultaneous duplicate requests crashed with a `500`, because the unique-constraint error was raised outside the error handler. Fixed by checking the key under the lock and adding a rollback-and-replay backstop.
4. **Money creation:** 20 transfers of `0.005` turned ₹1000.00 into ₹1000.20. Fixed by validating amounts at the API boundary.

## Run it locally

**Requirements:** Python 3.14 (what the project is built and tested on), Docker Desktop, Git.

```bash
git clone https://github.com/gaurniharika2005-lang/ledgerflow.git
cd ledgerflow
python -m venv venv
# Windows (PowerShell):  .\venv\Scripts\Activate.ps1
# macOS / Linux:         source venv/bin/activate
pip install -r requirements.txt
docker compose up -d      # development database (port 5432) and test database (port 5433)
```

Create a `.env` file in the project root:

```
DATABASE_URL=postgresql://ledgerflow_user:ledgerflow_pass@localhost:5432/ledgerflow_db
JWT_SECRET_KEY=<a long random string>
```

Generate a key with `python -c "import secrets; print(secrets.token_hex(32))"`. The database credentials above belong to the local development container only.

```bash
alembic upgrade head
uvicorn main:app --reload
```

Open http://127.0.0.1:8000/docs.

**Trying it out:** there are no deposit endpoints yet, so to test a transfer, give a wallet a balance directly:

```sql
UPDATE wallets SET balance = 1000
WHERE user_id = (SELECT id FROM users WHERE email = 'you@example.com');
```

To try the admin endpoints, promote a user the same way (`UPDATE users SET role = 'admin' WHERE email = '...'`). There is deliberately no API endpoint for becoming an admin.

**Run the tests** (needs the test database from `docker compose up -d`):

```bash
pytest -v
```

## Deployment

The API is containerized (see `Dockerfile`). Render builds the image from this repository and redeploys on every push to `main`. The database is a managed Neon PostgreSQL instance in the same AWS region as the API. `DATABASE_URL` and `JWT_SECRET_KEY` are set as environment variables in Render's dashboard.

## Current limitations and roadmap

- Balances are updated in place; there is no double-entry ledger or reconciliation check yet
- No deposit, withdrawal, or reversal endpoints; balances are seeded with SQL in demos
- Single currency (INR)
- Access tokens only (15-minute expiry); no refresh tokens yet
- No rate limiting yet
- Fraud rules are simple fixed thresholds, not a learned model
- No frontend; the Swagger UI is the interface

## Author

Built by Niharika Gaur ([github.com/gaurniharika2005-lang](https://github.com/gaurniharika2005-lang)).
