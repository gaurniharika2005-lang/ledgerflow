# LedgerFlow

![CI](https://github.com/gaurniharika2005-lang/ledgerflow/actions/workflows/ci.yml/badge.svg)

A digital wallet and payment-processing backend. Users register, get a wallet automatically, and transfer money to each other. Every movement of money is recorded in a double-entry-style ledger, so the books can be checked at any time. Transfers are atomic, safe to retry, and safe under concurrent load. A rule-based fraud check flags suspicious transfers for admin review, and admins can reverse transactions without ever rewriting history.

**Live API:** https://ledgerflow-api-yio7.onrender.com/docs (interactive Swagger UI)

> The live API runs on free tiers (Render and Neon) that sleep when idle, so the first request after a quiet period can take several seconds.

## Why this project

Payment systems have a few problems that ordinary CRUD apps don't. LedgerFlow is built around these:

- **A retried request must never charge twice.** Networks fail and clients retry, so every money movement carries an idempotency key.
- **Two requests touching the same balance must not corrupt it.** Wallet rows are locked, in a fixed order, while money moves.
- **Money amounts must be exact.** Amounts are validated at the API boundary so the database never has to round.
- **The books must provably balance.** Every movement writes signed ledger entries that sum to zero, and a reconciliation check verifies the whole system.
- **Mistakes must be correctable without erasing history.** Reversals are new transactions that point back at the original.
- **Suspicious activity needs a paper trail and a human decision.** Transfers that trip a rule are flagged with a reason and queued for admin review.

Each guarantee above has automated tests that attack it (see [What the tests prove](#what-the-tests-prove)).

## Features

- **Authentication:** registration, login, JWT access tokens (15-minute expiry), protected routes, role-based access (user / admin)
- **Wallets:** created automatically at registration; balances stored as exact decimals
- **Transfers:** `POST /transactions/transfer` is idempotent, row-locked, and atomic
- **Ledger:** every transfer, deposit, withdrawal, and reversal writes two signed entries (money out, money in) that sum to zero, each with the balance after the movement
- **Deposits and withdrawals:** admin-only endpoints that move money between a user and a system account, so the total of all balances is always exactly zero
- **Reversals:** an admin can reverse a transaction at most once, as a new compensating transaction
- **Reconciliation:** an admin endpoint that checks wallet balances against ledger entries and reports any mismatch
- **History:** a user's sent and received transactions, newest first
- **Fraud rules:** a transfer is flagged if the amount exceeds a threshold or the sender makes too many transfers in a short window. A flagged transfer still completes, and is recorded with the reason for review
- **Admin review:** list open flags and mark each as cleared or confirmed
- **Health checks:** `/health` (is the process up?) and `/health/db` (can it reach the database?)
- **Tests and CI:** 34 automated tests, including concurrency tests, run against a real PostgreSQL instance on every push

## Architecture

```
Client (Swagger UI or any HTTP client)
        |  HTTPS
        v
FastAPI app  (Docker container on Render, Virginia)
  routers:       auth | wallets | transactions | admin
  dependencies:  get_current_user -> require_admin
  core logic:    app/ledger.py (move_money: lock, check, write entries)
                 app/reconciliation.py | fraud rules | bcrypt | JWT
        |  SQLAlchemy 2.0 (psycopg2)
        v
PostgreSQL 16  (Neon, us-east-1)
  users | wallets | transactions | ledger_entries | fraud_flags
  schema managed with Alembic migrations
```

All money movement (transfers, deposits, withdrawals, reversals) goes through one function, `move_money`, so every path gets the same locking, idempotency, and ledger writes.

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
| POST | `/admin/deposits?idempotency_key=<uuid>` | admin | Credit a user's wallet from the system account |
| POST | `/admin/withdrawals?idempotency_key=<uuid>` | admin | Debit a user's wallet back to the system account |
| POST | `/admin/transactions/{id}/reverse?idempotency_key=<uuid>` | admin | Reverse a transaction (at most once) |
| GET | `/admin/reconciliation` | admin | Verify the ledger's invariants |
| GET | `/admin/flags` | admin | List open fraud flags |
| POST | `/admin/flags/{flag_id}/review` | admin | Mark a flag `cleared` or `confirmed` |
| GET | `/health` | public | Liveness check |
| GET | `/health/db` | public | Readiness check (runs `SELECT 1`) |

Full request and response schemas are in the interactive docs at `/docs`.

## Design decisions

1. **Money is `NUMERIC(18,2)`, never a float.** Floating-point numbers can't represent most decimal amounts exactly.
2. **Amounts are validated at the API boundary.** A transfer amount must be greater than zero, finite, at most 18 digits, and have at most 2 decimal places. Invalid amounts get `422`. Without this, an amount like `0.005` is accepted and the database rounds the sender's and receiver's balances in different directions, creating money (see the bugs below).
3. **Idempotency by a unique constraint plus a check under lock.** The client sends a UUID with each money movement, and `transactions.idempotency_key` is `UNIQUE`. The key is checked *after* the wallet locks are held, so a racing duplicate sees the winner's committed transaction and returns it instead of processing again. If an insert still hits the unique constraint, the handler rolls back and returns the original transaction.
4. **A key can only replay the request it was first used for.** Reusing a key with a different sender, receiver, or amount returns `422`, so a client bug can't silently receive the wrong transaction and another user can't read someone else's.
5. **Row-level locking in a fixed order.** Both wallets are loaded in a single `SELECT ... FOR UPDATE` query ordered by wallet id, so two movements never take locks in opposite orders (no deadlocks). The rows are loaded with `populate_existing()` so the balance checked is the freshly locked value, not a copy cached earlier in the session.
6. **Atomic writes.** The debit, the credit, the transaction record, the ledger entries, and any fraud flag commit together or not at all.
7. **Signed ledger entries.** Each movement writes one negative entry (money left a wallet) and one positive entry (money arrived), plus the wallet's balance after the movement. Entries are never edited or deleted. A wallet's balance must equal the sum of its entries.
8. **A system account for the outside world.** Deposits and withdrawals move money between a user and a special system wallet that is allowed to go negative. Because every movement is a pair of opposite entries, the sum of all wallet balances, including the system wallet, is always exactly zero. The system account's email (`system`) is not a valid email address, so the API can never register it or name it as a transfer recipient, and it can't log in.
9. **Reversals are compensating transactions.** A reversal moves the money back the way it came, as a new transaction with its own entries and a `reverses_transaction_id` pointing at the original. A unique constraint on that column, plus a check made while holding the wallet locks, ensures at-most-once reversal even with racing admins. A reversal is refused if the receiver no longer has enough funds (so customer wallets never go negative), a reversal can't itself be reversed, and reversals skip the fraud rules.
10. **Reconciliation checks three invariants.** Each wallet's balance equals the sum of its entries, each transaction has exactly two entries that sum to zero, and all balances sum to zero. It *detects* inconsistency, and it doesn't prevent tampering, which is a separate job for access control.
11. **UUID primary keys.** Sequential IDs leak information, such as how many users or transactions exist.
12. **Flag and review, don't block.** Simple fixed thresholds produce false positives, so a flagged transfer completes and a person decides.
13. **Admin checks hit the database.** `require_admin` reads the role from the database, not from the token, so a demoted admin loses access immediately. Non-admins get `403`, not `401`, because they are authenticated but not permitted.
14. **Login errors are generic.** "Incorrect email or password" is returned whether the email or the password is wrong, so the API doesn't reveal which emails are registered.
15. **Response models exclude secrets.** The password hash is not a field on any response schema.
16. **Two health endpoints.** `/health` doesn't touch the database, so a platform polling it won't keep the database awake. `/health/db` is for diagnosing connectivity.
17. **Isolated tests.** Tests run against a separate PostgreSQL database, with the schema dropped and recreated before each test. FastAPI's dependency override swaps in the test session.
18. **Configuration from the environment.** Secrets are never in the repository or the Docker image (`.env` is git-ignored and docker-ignored).
19. **Deploy order for schema changes.** Migrations are additive and backward compatible, and they are applied to the database before the code that uses them is deployed.

## What the tests prove

**Concurrency and idempotency**
- **No overdraft under load:** 15 parallel transfers of ₹100 from a ₹1000 wallet result in exactly 10 successes, 5 rejections, a sender balance of 0, and a receiver balance of ₹1000.
- **No deadlocks:** 20 simultaneous transfers in opposite directions between two wallets all succeed, and the total money is unchanged.
- **Idempotency under a race:** 10 simultaneous requests with the same key return the same transaction and charge exactly once.
- **Key misuse is rejected:** replaying a key with a different amount, or from a different user, returns `422` and moves no money.
- **Amounts can't create money:** invalid amounts (`0.005`, `0`, `-5`, `NaN`, `Infinity`, `1e30`, `abc`) are rejected with `422` and leave balances unchanged.

**Ledger**
- **Balanced entries:** each transfer writes exactly two entries that sum to zero, with the right balance after each. After 15 parallel transfers, only the 10 successful ones wrote entries.
- **Zero-sum under load:** after concurrent deposits, opposite-direction transfers, and withdrawals, the sum of all balances (system account included) is exactly zero and every wallet matches its entries.
- **System account is created once:** 10 simultaneous first deposits produce exactly one system wallet.
- **Access control:** non-admins get `403` on deposits, withdrawals, reversals, and reconciliation; the system account can't be registered or named as a recipient.

**Reconciliation and reversals**
- **Detection:** a balance edited without a ledger entry, and a transaction with an extra entry, are both reported (the first with the exact difference).
- **No false alarms:** 20 reconciliation requests run alongside 20 concurrent transfers all report a balanced ledger.
- **Reversals restore balances** and leave the original entries untouched.
- **At most once:** 10 simultaneous reversals of one transaction produce exactly one success and nine `409`s.
- **Safe refusal:** a reversal is refused when the receiver already spent the money, and a reversal can't be reversed.

The concurrency tests are timing-based, so after changing money-movement code I run them repeatedly.

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

**Trying it out:** there is deliberately no API endpoint for becoming an admin. Register a user, then promote it directly in the database:

```sql
UPDATE users SET role = 'admin' WHERE email = 'you@example.com';
```

Log in as that user and use `POST /admin/deposits` to fund any wallet. Then transfer money with `POST /transactions/transfer`, reverse it with `POST /admin/transactions/{id}/reverse`, and check `GET /admin/reconciliation`. Each money-moving call needs a fresh UUID as `idempotency_key`.

**Run the tests** (needs the test database from `docker compose up -d`):

```bash
pytest -v
```

## Deployment

The API is containerized (see `Dockerfile`). Render builds the image from this repository and redeploys on every push to `main`. The database is a managed Neon PostgreSQL instance in the same AWS region as the API. `DATABASE_URL` and `JWT_SECRET_KEY` are set as environment variables in Render's dashboard.

## Current limitations and roadmap

- No real payment gateway: deposits and withdrawals are admin operations that move ledger balances, not real money
- All deposits and withdrawals lock the single system wallet, so they queue behind each other (a hot-account limit; real systems shard it)
- Reconciliation scans the whole ledger, which is fine at this scale; at larger scale it would run incrementally from a checkpoint
- Transaction history is not paginated
- Single currency (INR)
- Access tokens only (15-minute expiry); no refresh tokens yet
- No rate limiting yet
- Fraud rules are simple fixed thresholds, not a learned model
- No frontend; the Swagger UI is the interface

## Author

Built by Niharika Gaur ([github.com/gaurniharika2005-lang](https://github.com/gaurniharika2005-lang)).
