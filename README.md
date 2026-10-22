# LedgerFlow

![CI](https://github.com/gaurniharika2005-lang/ledgerflow/actions/workflows/ci.yml/badge.svg)

A digital wallet and payment-processing backend. Users register, get a wallet automatically, and transfer money to each other. Transfers are atomic, safe to retry, and screened by a rule-based fraud check, with an admin workflow for reviewing flagged transactions.

**Live API:** https://ledgerflow-api-yio7.onrender.com/docs (interactive Swagger UI)

> The live API runs on free tiers (Render and Neon) that sleep when idle, so the first request after a quiet period can take several seconds.

## Why this project

Payment systems have a few problems that ordinary CRUD apps don't. LedgerFlow is built around three of them:

- **A retried request must never charge twice.** Networks fail and clients retry, so every transfer carries an idempotency key.
- **Two requests touching the same balance must not corrupt it.** Wallet rows are locked while a transfer runs.
- **Suspicious activity needs a paper trail and a human decision.** Transfers that trip a rule are flagged with a reason and queued for admin review.

## Features

- **Authentication:** registration, login, JWT access tokens (15-minute expiry), protected routes, role-based access (user / admin)
- **Wallets:** created automatically at registration; balances stored as exact decimals
- **Transfers:** `POST /transactions/transfer` is idempotent, row-locked, and atomic
- **History:** a user's sent and received transactions, newest first
- **Fraud rules:** a transfer is flagged if the amount exceeds a threshold or the sender makes too many transfers in a short window. A flagged transfer still completes, and is recorded with the reason for review
- **Admin review:** list open flags and mark each as cleared or confirmed
- **Health checks:** `/health` (is the process up?) and `/health/db` (can it reach the database?)
- **Tests and CI:** 7 automated tests run against a real PostgreSQL instance on every push

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
| Testing | pytest, FastAPI TestClient, GitHub Actions |
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

1. **Money is `NUMERIC(18,2)`, never a float.** Floating-point numbers can't represent most decimal amounts exactly, and rounding errors in a ledger are unacceptable.
2. **Idempotency by a unique constraint.** The client sends a UUID with each transfer, and `transactions.idempotency_key` is `UNIQUE`. Replaying the same key returns the original transaction without moving money again. If two identical requests race, the database rejects the second at commit and its whole transaction is rolled back (returned as `409`).
3. **Row-level locking.** Both wallets are loaded with `SELECT ... FOR UPDATE` before the balance check, so a concurrent transfer on the same wallet waits instead of reading a stale balance. The check happens after the lock is held.
4. **Atomic writes.** The debit, the credit, the transaction record, and any fraud flag commit together or not at all.
5. **UUID primary keys.** Sequential IDs leak information, such as how many users or transactions exist.
6. **Flag and review, don't block.** Simple fixed thresholds produce false positives, so a flagged transfer completes and a person decides. Blocking is reserved for much higher-confidence rules.
7. **Admin checks hit the database.** `require_admin` loads the user and reads the role from the database, not from the token. If someone is demoted, access ends immediately instead of when their token expires. Non-admins get `403`, not `401`, because they are authenticated but not permitted.
8. **Login errors are generic.** "Incorrect email or password" is returned whether the email or the password is wrong, so the API doesn't reveal which emails are registered.
9. **Response models exclude secrets.** The password hash is not a field on any response schema, so it can't leak through a serialization mistake.
10. **Two health endpoints.** `/health` doesn't touch the database, so a platform polling it won't keep the database awake. `/health/db` is for diagnosing connectivity.
11. **Isolated tests.** Tests run against a separate PostgreSQL database. Tables are created and dropped around each test, and FastAPI's dependency override swaps in the test session, so the real endpoint code runs unchanged.
12. **Configuration from the environment.** Secrets are never in the repository or the Docker image (`.env` is git-ignored and docker-ignored). They are supplied at runtime.

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

- No deposit or withdrawal endpoints; balances are seeded with SQL in demos
- Single currency (INR)
- Access tokens only (15-minute expiry); no refresh tokens yet
- No rate limiting yet
- Fraud rules are simple fixed thresholds, not a learned model
- Row locking is implemented, but there is no automated test that fires parallel transfers at one wallet yet
- No frontend; the Swagger UI is the interface

## Author

Built by Niharika Gaur ([github.com/gaurniharika2005-lang](https://github.com/gaurniharika2005-lang)).
