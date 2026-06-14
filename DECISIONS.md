# Architectural Decision Log

This document records the key engineering decisions made during development of the Shared Expenses application. Each entry includes what was considered, what was chosen, and why. This log exists so that any decision can be explained and defended in a technical review.

---

## 1. Database: PostgreSQL over SQLite

- **Decision**: Use PostgreSQL as the production database.
- **Alternative**: SQLite.
- **Why Chosen**: SQLite serialises all writes through a single file lock. Under concurrent HTTP requests (multiple flatmates using the app at the same time) this causes `database is locked` errors. PostgreSQL handles concurrent writes correctly, enforces foreign key constraints reliably, and supports JSONB columns used in `audit_logs`. It is also the standard choice for hosted environments like Neon and Render.
- **Tradeoff**: Requires a running PostgreSQL server (or cloud connection string) for local development. Mitigated by using SQLite in-memory for all unit tests.

---

## 2. Monetary Amounts: Integer Paise instead of Float

- **Decision**: Store all money as integers in paise (₹1 = 100 paise). Never store rupees as a float.
- **Alternative**: `FLOAT` or `DECIMAL` columns storing rupee values directly.
- **Why Chosen**: IEEE 754 floating-point arithmetic accumulates binary rounding errors. `0.1 + 0.2` in Python evaluates to `0.30000000000000004`. Over dozens of expense rows this drift compounds and produces incorrect balances. Integer paise arithmetic is exact. When a split produces a remainder (e.g. ₹1 split three ways = 33p + 33p + 34p) the extra paisa is deterministically assigned to the payer, so every split sums exactly to the original total.
- **Tradeoff**: All display logic must divide by 100 to show rupee values. A small formatting helper handles this everywhere.

---

## 3. CSV Staging Area Before Ledger Commit

- **Decision**: Imported CSV rows go into a `staged_expenses` table with `PENDING_APPROVAL` status. They are only promoted to the production ledger after a user explicitly approves them.
- **Alternative**: Auto-correct anomalies silently and insert directly.
- **Why Chosen**: The assignment specifies that Meera requires approval before anything is modified, skipped, or merged. Silent auto-correction would violate this rule and could corrupt historical balances with wrong assumptions. The staging layer gives flatmates full visibility and veto power over every imported row.
- **Tradeoff**: More tables, more routes, and a more complex import workflow. Worth the complexity because it is a hard requirement.

---

## 4. Session Auth: HMAC-Signed Cookies instead of JWT

- **Decision**: Sessions are tracked via a signed cookie containing `user_id.hmac_signature`, verified server-side on every request.
- **Alternative**: JWT tokens stored in `localStorage` or `sessionStorage`.
- **Why Chosen**: JWTs in `localStorage` are readable and stealable by any JavaScript running on the page (XSS). Cookies with `HttpOnly=True` are completely inaccessible to JavaScript. The HMAC signature (SHA-256, server secret key) prevents a user from modifying their `user_id` value manually. `SameSite=Lax` blocks the cookie from being sent in cross-site requests, reducing CSRF risk. `Secure=True` is enforced in production to prevent transmission over HTTP.
- **Tradeoff**: Stateful on the server side (must validate every request). No built-in expiry mechanism without adding a timestamp to the cookie payload — acceptable for this scope.

---

## 5. Framework: FastAPI instead of Flask or Django

- **Decision**: Use FastAPI as the web framework.
- **Alternative**: Flask (lighter), Django (heavier, batteries-included).
- **Why Chosen**: FastAPI provides automatic input validation via Pydantic, async request handling, and auto-generated API docs with zero extra configuration. Flask would require adding validation libraries manually. Django's ORM and admin system would conflict with the SQLAlchemy setup already designed for this schema. FastAPI occupies the right middle ground for this project size.
- **Tradeoff**: FastAPI's async model requires care when mixing sync database calls. Resolved by using synchronous SQLAlchemy sessions within standard route handlers.

---

## 6. ORM: SQLAlchemy instead of Raw SQL

- **Decision**: Use SQLAlchemy ORM for all database access.
- **Alternative**: Raw SQL strings with `psycopg2` or `asyncpg`.
- **Why Chosen**: SQLAlchemy provides a Python-level schema definition (`models.py`) that serves as the single source of truth for table structure. It handles query building, relationship loading, and session lifecycle. Raw SQL would require maintaining SQL strings in separate files, risking drift between schema definitions and queries.
- **Tradeoff**: SQLAlchemy adds an abstraction layer that can make complex queries harder to debug. In those cases (e.g. the balance trace engine) raw-style column expressions were used within SQLAlchemy's query API for clarity.

---

## 7. Password Hashing: Native bcrypt instead of passlib

- **Decision**: Use the `bcrypt` package directly instead of `passlib.CryptContext`.
- **Alternative**: `passlib` wrapping bcrypt.
- **Why Chosen**: `passlib` is a wrapper library that adds an extra dependency layer. On Python 3.11 and 3.12, `passlib`'s internal bcrypt references trigger deprecation warnings and, in some environments, fail silently during hash verification. Using `bcrypt` directly (`bcrypt.hashpw`, `bcrypt.checkpw`) removes the intermediary, is explicit, and is stable across Python versions.
- **Tradeoff**: Slightly more verbose call sites. No material downside.

---

## 8. Balance Caching: Snapshot Table instead of On-Demand Calculation

- **Decision**: After every expense, payment, or import promotion, recalculate and store each user's net balance in a `balance_snapshots` table.
- **Alternative**: Calculate balances on-demand by scanning all expenses and payments every time the dashboard loads.
- **Why Chosen**: On-demand calculation requires scanning potentially hundreds of rows on every page load. With five flatmates and months of history, this becomes a full table scan on every request. Snapshots reduce the dashboard read to a single indexed lookup per user.
- **Tradeoff**: Snapshots can become stale if an update bypasses the snapshot refresh logic. Mitigated by always triggering a snapshot refresh inside the same database transaction as any ledger change.

---

## 9. Debt Simplification: Greedy Net-Balance Algorithm

- **Decision**: Simplify group debts by computing each person's net position and then greedily matching the largest debtor to the largest creditor.
- **Alternative**: Show raw pairwise debts (A owes B, B owes C, C owes A).
- **Why Chosen**: Raw pairwise debts produce circular payment chains. If Aisha owes Rohan ₹500 and Rohan owes Priya ₹500, the naive view shows two payments. The net-balance algorithm collapses this to one: Aisha pays Priya ₹500. This minimises the number of transactions needed to settle the group.
- **Tradeoff**: The simplified settlements are mathematically equivalent but lose the original pairwise attribution. For auditability, the raw transaction history remains fully intact in the ledger.

---

## 10. Test Database: SQLite In-Memory instead of PostgreSQL

- **Decision**: Unit tests run against an in-memory SQLite database, not a live PostgreSQL connection.
- **Alternative**: Spin up a test PostgreSQL instance (Docker or Neon branch) for every test run.
- **Why Chosen**: An in-memory SQLite database requires no external infrastructure, starts in milliseconds, and is destroyed automatically after each test session. This keeps the test suite runnable offline and without credentials. PostgreSQL-specific features (JSONB, server-side functions) are not exercised in unit tests — they are validated through integration testing against the live Neon instance.
- **Tradeoff**: Tests will not catch PostgreSQL-specific constraint or type errors. Acceptable because the schema uses only standard SQL types that SQLite supports.

---

## 11. Split Remainder Allocation: Assign to Payer

- **Decision**: When dividing an amount that does not divide evenly (e.g. ₹100 among 3 people = 33p + 33p + 34p), the extra paisa is assigned to the payer's own share.
- **Alternative**: Assign to the first alphabetically, or distribute randomly.
- **Why Chosen**: The payer is already the person who is owed money by everyone else. Giving them the extra paisa means they are owed very slightly less, which is the most conservative and least controversial allocation. It is deterministic, reproducible, and easy to explain.
- **Tradeoff**: The payer's share may be ₹0.01 more than other shares on some rows. Immaterial at any realistic expense value.

---

## 12. Varying Tenure: Active Membership Window Query

- **Decision**: When splitting an expense, only include flatmates who were active members of the group on the expense date (i.e. `joined_date <= expense.date` and `left_date IS NULL OR left_date >= expense.date`).
- **Alternative**: Always split among all registered members regardless of tenure.
- **Why Chosen**: Splitting a January electricity bill equally with someone who moved in March is incorrect and unfair. The active window query ensures each person pays only for the period they were present.
- **Tradeoff**: Adds a date-range join on every expense creation. Negligible performance cost for the data volumes in this application.

---

## 13. Authorization: Route-Level Group Membership Checks

- **Decision**: Every route that accesses group data (expenses, payments, balances, import batches) must verify that the requesting user is a member of the group being accessed, in addition to verifying authentication.
- **Alternative**: Trust that authenticated users will only navigate to groups they belong to. Rely on the UI not exposing links to other groups.
- **Why Chosen**: Authentication (who are you?) and authorization (are you allowed here?) are distinct security checks. Relying on UI navigation to prevent access to unauthorized resources is Insecure Direct Object Reference (IDOR) — listed in the OWASP Top 10. Any user who knows or guesses a group ID can construct the URL manually. A missing membership check is a systemic security gap, not a UI limitation.
- **Tradeoff**: One additional database query per group-scoped request. Cost is negligible. Correctness is mandatory.

---

## 14. Dependencies: Separate Production and Development Requirements

- **Decision**: `pytest` and `pytest-mock` belong in a `requirements-dev.txt` file, not in `requirements.txt`. Production deployments should not install test frameworks.
- **Alternative**: Keep all dependencies in a single `requirements.txt` for simplicity.
- **Why Chosen**: Every unnecessary package in a production image is a potential CVE surface. Test frameworks are not needed at runtime and add build time, image size, and dependency risk. The separation also clarifies intent: `requirements.txt` is what the application needs to run; `requirements-dev.txt` is what a developer needs to work on it.
- **Tradeoff**: Slightly more complex setup instructions — developers must install both files. Mitigated by a clear note in the README.
