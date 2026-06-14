# Architectural Decision Log

This document records the key design and engineering decisions made during the development of the Shared Expenses application.

---

## 1. Database System: PostgreSQL (Neon)
- **Decision**: Use PostgreSQL instead of SQLite.
- **Alternatives Considered**: SQLite.
- **Why Chosen**: SQLite has concurrency limitations (e.g. `database is locked` errors during concurrent writes) and lacks native JSONB support for auditing. PostgreSQL handles high concurrency, enforces strict constraints, and is fully supported by Neon serverless cloud hosting.
- **Security Tradeoffs**: Requires network configuration and credentials management, but provides isolated environments and robust access control.

---

## 2. Monetary Precision: Integer Paise (Minor Units)
- **Decision**: Store all currency amounts as integers representing paise (1 INR = 100 paise).
- **Alternatives Considered**: IEEE 754 Floating-Point (`REAL`/`FLOAT`), text/numeric types without scaling.
- **Why Chosen**: Floating point arithmetic accumulates binary rounding drift (e.g. `0.1 + 0.2 != 0.3`). Integers guarantee exact addition/subtraction. Remaining division fractions (e.g. splitting 100 paise between 3 people) are allocated deterministically to the payer, ensuring sums match.

---

## 3. Stateful CSV Ingestion Staging Area
- **Decision**: Import CSV records into a `staged_expenses` table with `PENDING_APPROVAL` status before promoting them.
- **Alternatives Considered**: Auto-skipping or auto-correcting anomalies directly into production.
- **Why Chosen**: Satisfies Meera's constraint ("Meera requires approval before anything is deleted, merged, modified, or skipped"). It prevents corrupting historical production ledger balances with auto-corrected assumptions.

---

## 4. Session Authentication: Signed Cookies (HMAC-SHA256) vs JWT Tokens
- **Decision**: Use cookie-based session tracking with cryptographically signed user IDs (format: `user_id.signature`).
- **Alternatives Considered**: JSON Web Tokens (JWT) stored in LocalStorage, raw unsigned cookie values.
- **Why Chosen**: 
  - **Security over JWT**: Standard JWTs are commonly stored in the browser's `localStorage` or `sessionStorage`, making them vulnerable to theft via Cross-Site Scripting (XSS) attacks. By contrast, cookies configured with `HttpOnly` are inaccessible to client-side scripts, protecting the session from XSS extraction.
  - **Cookie Security Settings**:
    - `HttpOnly = True`: Blocks JavaScript cookie access.
    - `SameSite = Lax`: Prevents cookie transmission in cross-site requests, mitigating Cross-Site Request Forgery (CSRF).
    - `Secure = True`: Configured dynamically to enforce HTTPS transport in production.
- **Tampering Prevention**: Session tampering is prevented by appending a secure hash signature using **HMAC-SHA256** and a server-side secret key. When a request arrives:
  1. The server splits the cookie value: `user_id` and `signature`.
  2. The server recalculates the signature using its secret key.
  3. Using `hmac.compare_digest` (to prevent timing attacks), the server verifies the signature matches. If mismatched or tampered, the session is invalidated instantly.
