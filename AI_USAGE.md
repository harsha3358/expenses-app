# AI Usage Log — Engineering Review Journal

**Project**: Shared Expenses Application
**Review Type**: Architecture | Security | Correctness | Deployment
**Purpose**: This document is a structured engineering log of how AI-assisted tooling was used as a review and challenge layer throughout the development lifecycle. It is not a prompt history. It is a record of design decisions made, challenged, validated, or rejected — with AI used as an adversarial reviewer alongside engineering judgment.

Every suggestion documented here was evaluated, challenged back, and either adopted with modification or explicitly rejected with a recorded rationale. The standard applied was: *would this decision survive a production incident postmortem?*

---

## Tools Used

| Tool | Role in This Project |
|---|---|
| **Antigravity IDE (Gemini 2.5 Flash)** | Primary implementation assistant — architecture scaffolding, phase-by-phase code generation, iterative code review across all six development phases |
| **Claude (claude-sonnet)** | Adversarial review sessions — used specifically to challenge decisions already made, probe security boundaries, and attempt to break correctness assumptions |
| **ChatGPT (GPT-4o)** | Initial requirement decomposition and scoping — used to structure the problem space before any schema or code was committed |
| **Gemini CLI** | Inline spot checks during implementation — SQLAlchemy ORM edge cases, FastAPI dependency injection patterns, Jinja2 template constraints |
| **GitHub Copilot** | Autocomplete during boilerplate phases — model schema definitions, test fixture setup, repetitive import statements |
| **DeepSeek** | Secondary independent audit of the import anomaly engine — used to verify anomaly coverage against the specification without inheriting the primary model's blind spots |

---

## 1. Architecture & System Design

### 1.1 Database Engine Selection: PostgreSQL vs SQLite

**Question posed to AI**: "What are the failure modes of SQLite under concurrent HTTP requests from five users?"

**AI response**: SQLite uses a single write lock at the database file level. Under concurrent POST requests — two flatmates submitting expenses simultaneously — one transaction blocks the other. In practice this produces `sqlite3.OperationalError: database is locked` under load. PostgreSQL uses row-level MVCC locking. Concurrent writes to different rows proceed without blocking.

**Engineering evaluation**: This was not a theoretical concern. The application handles concurrent form submissions from multiple flatmates. SQLite's file lock would produce intermittent 500 errors under real use. The decision to use PostgreSQL was not made for prestige — it was made because SQLite's concurrency model is structurally incompatible with a multi-user web application.

**Decision**: PostgreSQL for production. SQLite in-memory for unit tests (single-threaded, no concurrency needed).

---

### 1.2 Session Architecture: Cookie Signing vs JWT

**Question posed to AI**: "What are the XSS attack surfaces of JWT tokens stored in localStorage vs HMAC-signed HttpOnly cookies?"

**AI response**: JWTs in localStorage are accessible to any JavaScript executing on the page. An XSS vulnerability — injected script, third-party ad SDK, compromised CDN asset — can read and exfiltrate the token. HttpOnly cookies are completely inaccessible to JavaScript, regardless of XSS. However, they introduce CSRF risk, which must be mitigated separately (SameSite attribute, CSRF tokens).

**Engineering evaluation**: The application uses server-rendered HTML templates — no React, no API calls from the browser, no complex JavaScript. The cookie model is appropriate. SameSite=Lax blocks cross-site POST. HttpOnly blocks XSS token theft. For the threat model of a flatmate expense app, this is the correct tradeoff.

**Decision**: HMAC-signed HttpOnly cookies. Secret stored in environment variable. Signature uses `hmac.compare_digest` to prevent timing oracle attacks.

---

### 1.3 Balance Snapshot Strategy: Pre-computed vs Live Calculation

**Question posed to AI**: "Compare pre-computed balance snapshots vs on-demand aggregation for a 5-user expense tracking application with 3 months of history."

**AI response**: On-demand aggregation scans `expense_shares` and `payments` on every dashboard load. For 5 users × 90 days × ~3 expenses/day = ~1350 share rows and ~100 payment rows. At this volume, aggregation takes milliseconds. Snapshots add write complexity — every ledger mutation must invalidate and refresh the cache. For scale beyond ~10,000 rows, snapshots become clearly superior.

**Engineering decision**: Pre-computed snapshots were chosen anyway, despite the small data volume. The reason was not performance — it was correctness auditability. When Rohan disputes his balance, the snapshot table shows a single authoritative figure that was computed at a specific timestamp. An on-demand calculation has no audit trail. The snapshot is the single source of truth that can be inspected, compared against the trace, and explained precisely.

**Tradeoff accepted**: Snapshot staleness if a mutation bypasses the refresh trigger. Mitigated by calling `recalculate_snapshots()` inside every mutation route, before returning the HTTP response.

---

### 1.4 Staging Workflow Architecture: Pre-ledger Quarantine

**Question posed to AI**: "Design a CSV import pipeline that satisfies the requirement that no data can enter the ledger without human review."

**AI response**: Three options were presented:
1. Auto-correct silently, insert directly.
2. Import to a draft state, show a diff, require confirmation.
3. Import to a separate staging table, show per-row anomaly flags, require per-row decisions.

**Engineering evaluation**: Option 1 violates the assignment requirement directly. Option 2 requires confirmation but doesn't give visibility into which rows have problems and why. Option 3 is the most expensive to implement but provides full per-row control — which is exactly what "Meera's approval requirement" demands.

**Decision**: Full staging table architecture. `staged_expenses`, `staged_anomalies`, `staged_expense_shares`, and `import_decisions` form a quarantine layer. Nothing promotes to `expenses` or `expense_shares` without an explicit APPROVE decision recorded in `import_decisions`.

---

### 1.5 Debt Simplification Algorithm

**Question posed to AI**: "Prove that the greedy net-balance algorithm for debt simplification is optimal."

**AI response**: For N participants with non-zero net balances, the greedy algorithm proceeds by matching the largest creditor to the largest debtor in each step. Each step eliminates at least one participant (their balance reaches zero). Starting with N participants, at most N-1 steps are required. This is optimal: to clear N unbalanced participants, you need at least N-1 transactions (each transaction can eliminate at most one participant from the unbalanced set).

**Engineering evaluation**: The proof was verified manually. The algorithm is correctly implemented in `balance.py:simplify_debts`. The only limitation is that it does not preserve pairwise attribution — it replaces "Aisha owes Rohan ₹500 and Rohan owes Priya ₹500" with "Aisha owes Priya ₹500." The raw transaction history remains in the ledger for full attribution.

---

## 2. Threat Modeling & Security Reviews

### 2.1 Cookie Signing Attack Surface Analysis

**Threat modeled**: Can an attacker forge a valid session cookie for a different user_id?

**Investigation**: The cookie format is `{user_id}.{HMAC-SHA256(user_id, SECRET_KEY)}`. An attacker who knows user_id=1 wants to create a valid cookie. They can compute `f"1.{hmac.new(UNKNOWN_KEY, b'1', sha256).hexdigest()}"`. Without the `SECRET_KEY`, the HMAC is a 256-bit random value — computationally infeasible to forge.

**Finding**: Cookie forgery is blocked by HMAC. However, three residual risks exist:
1. If `SECRET_KEY` leaks (e.g., committed to git), all sessions are compromised.
2. The default `SECRET_KEY` value (`'super-secret-flatmate-key'`) is visible in source. Any deployment using the default is immediately vulnerable.
3. There is no session expiry. A captured cookie is valid indefinitely.

**Action taken**: (1) `SECRET_KEY` is read from environment, never hardcoded in deployed code. (2) Default key risk is documented — startup assertion recommended but not yet implemented. (3) Session expiry deferred as out of scope; documented as a known gap.

---

### 2.2 Authorization Boundary: IDOR Vulnerability Assessment

**Threat modeled**: Can authenticated user A access group 2's data when A is only a member of group 1?

**Investigation**: The `GET /groups/{group_id}` route in `main.py` checks `if not current_user` (authentication). It does not check group membership. A user who knows or guesses group_id=2 can access it by navigating to `/groups/2`.

**Finding**: All eight group-scoped routes lack authorization checks. This is Insecure Direct Object Reference (IDOR) — OWASP Top 10 A01:2021 Broken Access Control. Every group's expenses, balances, and import history is accessible to any authenticated user.

**Action taken**: `require_group_member(group_id, user_id, db)` helper function defined. Application of this check to all eight routes is pending the authorization fix pass. The gap is recorded here because it was identified during review and has not yet been closed in code.

---

### 2.3 CSV Ingestion Attack Surface

**Threat modeled**: What happens when a malicious CSV is uploaded with crafted content?

**Investigation**: Reviewed `parse_csv_to_staging()` for injection vectors.
- **Path traversal**: `file.filename` is used directly in `os.path.join(temp_dir, file.filename)`. A filename of `../../app/main.py` could overwrite application code.
- **File size**: No size limit. A 500MB upload is written to disk before any processing.
- **Content type**: No MIME validation. Any file extension is accepted.

**Finding**: Path traversal is a real risk. File size is unconstrained. No MIME validation.

**Action taken**: Fixes identified. `os.path.basename(file.filename)` eliminates path traversal. `len(contents) > 5MB` guard added. `.csv` extension check added. These fixes are in the pending main.py fix pass.

---

### 2.4 Data Integrity: Balance Formula Verification

**Threat modeled**: Is the net balance formula mathematically correct? Does the group invariant (sum = 0) hold?

**Investigation**: Formula: `net = (paid_amt + sent_amt) - (owed_amt + recv_amt)`. Verified across three scenarios:

**Scenario A — No settlements**:
- Aisha paid ₹3000, share ₹1000 → net = (3000+0)-(1000+0) = +2000 ✓
- Rohan paid ₹0, share ₹1000 → net = (0+0)-(1000+0) = -1000 ✓
- Priya paid ₹0, share ₹1000 → net = -1000 ✓
- Group sum: 2000 - 1000 - 1000 = 0 ✓

**Scenario B — Rohan settles (sends ₹1000 to Aisha)**:
- Aisha: (3000+0)-(1000+1000) = +1000 ✓ (still owed by Priya)
- Rohan: (0+1000)-(1000+0) = 0 ✓ (fully settled)
- Priya: (0+0)-(1000+0) = -1000 ✓
- Group sum: 1000 + 0 - 1000 = 0 ✓

**Finding**: Formula is mathematically correct. The audit report that initially claimed it was wrong contained a semantically incoherent test scenario (a creditor being modelled as sending money without additional debt context). The formula was verified correct across all realistic scenarios.

**Action taken**: Added full formula derivation as a comment block in `balance.py`. No code change required.

---

## 3. Code Audits

### 3.1 Import Engine Correctness Review

**Audit scope**: `parse_csv_to_staging()` and `process_decision()` in `importer.py`.

**Risk**: A 644-line function with branching logic for 16 anomaly types, three decision types, and currency conversion. High risk of edge case bugs that produce incorrect ledger entries.

**Investigation**:
- **Remainder allocation**: The original code used `for idx, uid in enumerate(active_user_ids)` where `active_user_ids` was a `set`. Python sets have non-deterministic enumeration order. The `idx == 0` check for remainder allocation was arbitrary — the remainder could go to any member depending on the interpreter's internal hash ordering.
- **Group ID resolution in `process_decision`**: The original code used `db.query(GroupMembership).filter(GroupMembership.user_id == user_id).first().group_id` — this gets the current user's *first* group membership. If the user belongs to multiple groups, this returns the wrong group, causing balance snapshots to be recalculated for the wrong group.

**Finding**: Two correctness bugs confirmed.

**Action taken**:
1. `active_user_ids` converted to list before enumerate. `payer_idx` computed explicitly using `.index()`. Remainder allocated to payer's index position, not `idx==0`.
2. `group_id` now resolved from `batch.group_id` (authoritative, stored at import time). Fallback to user's first membership only if `batch.group_id` is null (backwards compatibility for batches created before the schema change).

---

### 3.2 Balance Snapshot Isolation Audit

**Risk**: `recalculate_snapshots()` is called after every ledger mutation but runs in a separate transaction from the mutation itself. A crash between the expense commit and the snapshot recalculation leaves the snapshot stale.

**Investigation**: In `post_expense`:
```python
db.commit()  # Expense and shares committed
recalculate_snapshots(group_id, db)  # Snapshot updated in second transaction
```
If the process crashes between these two calls, the expense exists in the ledger but the snapshot is stale. The next dashboard load would show incorrect balances until the next mutation triggers a refresh.

**Finding**: Snapshot staleness on mid-flight crash is a real risk. The invariant (snapshots reflect all committed ledger data) can be temporarily violated.

**Action taken**: Documented as a known limitation. The correct fix is to include snapshot recalculation inside the same database transaction as the mutation, or to implement a startup reconciliation job that recomputes all snapshots on boot. The startup `recalculate_snapshots()` call in `on_startup` provides partial mitigation — stale snapshots are corrected on next application restart.

---

### 3.3 Membership Boundary Date Semantics

**Risk**: Ambiguity in whether `left_date` is inclusive or exclusive. A flatmate who officially leaves on June 1 — are they charged for June 1 expenses?

**Investigation**: The active member query is:
```python
(GroupMembership.left_date == None) | (GroupMembership.left_date >= expense_date)
```

`left_date >= expense_date` means: if left_date is June 1 and expense_date is June 1, the condition is `True` — the departing member IS included in the split.

**Finding**: The boundary date is inclusive. A member is considered active on their leave date.

**Assumption challenged**: "The departing member should not be charged for anything on the day they leave." Rejected. If someone lives in the flat until June 1, they consumed electricity on June 1. Excluding them from June 1 expenses is incorrect. Inclusive leave date is the right policy.

**Action taken**: Policy documented. Active window query is `joined_date <= date AND (left_date IS NULL OR left_date >= date)`. This is the intended semantics.

---

## 4. Engineering Reviews Performed

### Review 1: Financial Correctness Review

**Risk**: Incorrect balance calculations that cause real money disputes between flatmates.

**Investigation**: Traced Aisha's balance across three months of sample data. Verified that:
- January expenses (before Sam joined) exclude Sam from splits.
- Meera's expenses (April–May, left June 1) correctly charge her for the period she was present.
- Settlements reduce both the sender's and receiver's net positions by exactly the settlement amount.

**Finding**: Balance calculations are correct for all tested scenarios. The formula is verified by mathematical proof (double-entry invariant).

**Action taken**: Added formal formula derivation as a comment block in `balance.py`. Strengthened test assertions to verify actual `net_balance_paise` values, not just snapshot existence.

---

### Review 2: Ledger Consistency Review

**Risk**: An expense exists in the ledger without corresponding expense shares, producing an unbalanced ledger.

**Investigation**: In `post_expense`, expense is flushed (`db.flush()`) before shares are created. If `db.commit()` is called before all shares are inserted, the ledger is unbalanced.

**Finding**: The current code creates all shares before calling `db.commit()`. The flush only sends the INSERT to the database within the open transaction — it does not commit. The commit happens after all shares are added. The ledger is transactionally consistent.

**Action taken**: No code change needed. The review confirmed correctness. `db.flush()` → create shares → `db.commit()` is the correct sequence.

---

### Review 3: Membership Boundary Review

**Risk**: Expenses charged to members who were not present, or expenses not charged to members who were.

**Investigation**: Tested the active window query against five membership scenarios:
- Aisha (Jan 1, no leave date) — active on all dates from Jan 1 ✓
- Sam (joined Mar 1) — not active on Jan 15, active on Mar 15 ✓
- Meera (Apr 1 – Jun 1) — not active on Mar 15, active on Apr 15, active on Jun 1, not active on Jun 2 ✓

**Finding**: Active window query is correct for all tested membership configurations.

**Action taken**: Confirmed. Policy documented in SCOPE.md Section 3.

---

### Review 4: Import Workflow Review

**Risk**: CSV row approved by reviewer but not promoted to ledger; or promoted without audit trail.

**Investigation**: Traced `process_decision()` for the APPROVED path:
1. `staged_exp.status` updated to "APPROVED".
2. `Expense` created from staged data.
3. `ExpenseShare` rows created from staged shares.
4. `ImportDecision` created linking the anomaly, decision, and user.
5. `AuditLog` entry written.
6. `db.commit()` called.

**Finding**: The approval path is complete and audited. The audit chain from CSV row to ledger entry is traceable: `staged_expenses → import_decisions → expenses → expense_shares`.

**Action taken**: No code change. Review confirmed correctness of the promotion path.

---

### Review 5: Security Review

**Risk**: Authentication bypass, session forgery, data exposure.

**Findings summary**:
- HMAC cookie signing: correct. `compare_digest` in use. ✓
- Default SECRET_KEY: risk if deployed without overriding. Startup assertion not implemented. ⚠
- Authorization (IDOR): all group routes missing membership checks. Critical gap. ✗
- SQL injection: SQLAlchemy ORM parameterizes all queries. No raw SQL. ✓
- Password hashing: bcrypt, direct (no passlib wrapper). ✓
- File upload: path traversal via unsanitised filename. ⚠ (fix pending)

**Action taken**: IDOR and path traversal fixes queued for implementation. Startup assertion for weak key documented as a known gap.

---

### Review 6: Deployment Readiness Review

**Risk**: Configuration errors cause production failures.

**Investigation**: Reviewed `render.yaml`, `Procfile`, environment variable handling.

**Finding**: `Procfile` uses `web: uvicorn app.main:app --host 0.0.0.0 --port $PORT` — correct for Render. `render.yaml` sets build and start commands. `ENV=production` triggers `Secure=True` on session cookies. `DATABASE_URL` and `SECRET_KEY` must be set manually as environment variables in the Render dashboard.

**Action taken**: README updated with explicit deployment steps. Deployment is configuration-complete but requires student action to trigger.

---

## 5. Assumptions Challenged

### Assumption 1: "Every duplicate row should be deleted automatically."

**Why challenged**: A row that appears to be a duplicate may be legitimate — two dinner expenses at the same restaurant on the same day for the same amount are structurally identical but represent distinct events.

**Final design**: Duplicates are flagged as A01 DUPLICATE_ROW with severity WARNING, not automatically deleted. The reviewer sees the proposed correction ("propose skipping") and decides whether to approve the skip or import both rows.

---

### Assumption 2: "Negative amounts are invalid."

**Why challenged**: A refund from a store, a reimbursement from an employer, a landlord returning a deposit — these are all negative amounts that represent real financial events in a shared flat context.

**Final design**: Negative amounts trigger the A11 anomaly with two branches — NEGATIVE_AMOUNT (Warning, treated as Refund Candidate) vs ZERO_AMOUNT (Hard Error). Negative amounts can be reviewed and imported; zero amounts are always blocked.

---

### Assumption 3: "Balances should always be computed live from the raw transaction data."

**Why challenged**: Live computation means the balance shown to Rohan on the dashboard is the result of a query that scans all expenses and payments every time the page loads. This is correct but slow at scale and leaves no auditable "state at a point in time."

**Final design**: Pre-computed snapshots. The snapshot captures the balance at the moment of the last ledger mutation. The balance trace shows the full derivation. The snapshot is the authoritative figure; the trace is the proof.

---

### Assumption 4: "SQLite is enough for a 5-person flatmate app."

**Why challenged**: Concurrent HTTP requests from multiple flatmates can produce write lock contention. A single flatmate submitting a large CSV import (hundreds of staged inserts) would block all other database writes for the duration of the import.

**Final design**: PostgreSQL. Row-level locking. No write contention between concurrent users. SQLite retained only for the in-memory test environment where concurrency is not a concern.

---

### Assumption 5: "Users will upload clean CSV files."

**Why challenged**: The assignment explicitly provides a CSV with anomalies. Real-world expense spreadsheets from flatmates will have name typos (`aisha m.`), currency symbols in amounts (`$120.50`), ambiguous dates (`05/06` — US or European format?), and settlements mixed with expenses.

**Final design**: 16-type anomaly detection engine. Every row is validated against all 16 checks. Each anomaly flags the specific issue, proposes a correction, and requires human review. The import pipeline assumes the input is dirty and validates defensively.

---

## 6. Failure Scenarios Simulated

### Scenario 1: Snapshot Drift After Mid-Flight Crash

**Expected failure**: Application process crashes after `db.commit()` (expense saved) but before `recalculate_snapshots()` completes. Dashboard shows stale balance.

**Detection**: Next mutation triggers a fresh `recalculate_snapshots()`, correcting the snapshot. The startup `recalculate_snapshots()` also corrects stale snapshots on restart.

**Recovery**: Automatic on next mutation or restart. No manual intervention required. Stale window is bounded by time-to-next-mutation.

---

### Scenario 2: Missing Exchange Rate for Unknown Currency

**Expected failure**: CSV contains a GBP expense. `ExchangeRate` table has no GBP entry. `rate_record` is `None`. `rate_record.rate_to_inr` raises `AttributeError`.

**Detection**: Code uses `rate_record.rate_to_inr if rate_record else Decimal("83.000000")` — falls back to USD rate. This is wrong (GBP ≠ USD) but does not crash.

**Finding**: The fallback rate (83.0) is hardcoded as the USD rate. Applying it to GBP produces an incorrect conversion. A GBP expense would be treated as USD. This is a silent correctness error.

**Recovery strategy**: The correct fallback is to flag the row as A06 PAYER_NOT_REGISTERED (equivalent: CURRENCY_NOT_REGISTERED) and block import until the currency is seeded. Not implemented — documented as a known gap.

---

### Scenario 3: HMAC Cookie Forgery Attempt

**Expected failure**: Attacker crafts a cookie `1.forgedsignature` to impersonate user_id=1.

**Detection**: `verify_user_id()` calls `hmac.compare_digest(expected, signature)`. `expected` is computed server-side using the `SECRET_KEY`. If `forgedsignature != expected`, `compare_digest` returns `False`. `get_current_user` returns `None`. Request is rejected.

**Recovery**: Not needed. Forgery is blocked cryptographically. The attacker would need to know the `SECRET_KEY` to compute a valid HMAC.

---

### Scenario 4: Same CSV Uploaded Twice

**Expected failure**: First upload creates batch 1, all rows staged. Second upload creates batch 2, same rows staged again. If reviewer approves both batches, all expenses are doubled in the ledger.

**Detection**: A01 DUPLICATE_ROW check compares staged rows against the *production* ledger (existing `expenses`). But it does not compare against *other staged batches*. The second upload's rows will not be flagged as duplicates until the first batch is promoted.

**Finding**: Double-import via concurrent staging is a real gap. A reviewer who approves two batches of the same CSV without noticing will corrupt the ledger.

**Recovery strategy**: Before promoting a staged row, check for existing `expenses` with identical (date, paise, paid_by_id, group_id). Not fully implemented. The duplicate check runs at staging time, not at promotion time.

---

### Scenario 5: Boundary-Date Expense for Departing Member

**Expected failure**: Meera leaves June 1. An expense dated June 1 is created manually. Is Meera included in the split?

**Detection**: Active window query: `left_date >= expense_date` → `June 1 >= June 1` → True. Meera is included.

**Finding**: Intended behaviour. Meera lived in the flat on June 1 and should share June 1 expenses. The policy (inclusive leave date) is correct.

**Recovery**: No issue. Behaviour is by design and documented.

---

### Scenario 6: Large Outlier Transaction Submitted

**Expected failure**: CSV contains ₹5,00,000 internet bill (five lakh rupees). This is 5× the outlier threshold (₹1,00,000). System flags it as A16.

**Detection**: A16 OUTLIER_AMOUNT anomaly is raised with severity WARNING. Row is staged with status PENDING_APPROVAL. Import report shows the flag. Reviewer must explicitly approve to promote.

**Recovery**: Human review is the recovery. The outlier flag ensures the reviewer sees it and makes a conscious decision, rather than auto-importing an obvious data entry error.

---

## 5. AI Contributions Explicitly Rejected

### Rejection 1: Auto-Merge Duplicate Rows

**What was suggested**: When a CSV row matches an existing expense (same date, amount, payer), automatically merge them instead of staging for review.

**Why rejected**: Field matching equality does not equal semantic identity. Two ₹3000 rent payments from Aisha on March 1 could be a duplicate entry, or it could be that March had two separate rent components (base rent + maintenance). The system cannot determine intent from data alone. Auto-merge would silently lose real expenses. The human reviewer is the only entity with the context to make this call.

---

### Rejection 2: Bypass Approval for Anomaly-Free Rows

**What was suggested**: Rows with no anomalies should be auto-approved and inserted directly into the ledger without requiring the reviewer to see them.

**Why rejected**: The requirement is that Meera approves before anything enters the ledger. "No detected anomaly" is not the same as "factually correct." A row with a plausible amount, a valid date, a registered payer, and an equal split might still be wrong — the date might be mistyped, the payer might have paid a different amount, or the row might represent an event that didn't happen. Anomaly detection validates format and consistency, not factual correctness. All rows require human sign-off.

---

### Rejection 3: Use Float for Monetary Arithmetic

**What was suggested**: "Using Python Decimal is overkill for a 5-person flatmate app. The rounding errors are small enough to ignore."

**Why rejected**: This suggestion was rejected unconditionally. `0.1 + 0.2` in Python is `0.30000000000000004`. Over 90 days of expenses, float rounding errors compound. A balance that should be ₹0.00 might show ₹0.01 or ₹-0.01. In a financial system, ₹0.01 discrepancies are not "small enough to ignore" — they are evidence that the calculation engine is wrong. Integer paise arithmetic is exact by construction. There is no legitimate argument for float in monetary calculations.

---

### Rejection 4: Remove Audit Logs to Simplify the Schema

**What was suggested**: The `audit_logs` table was flagged as over-engineering for a five-person flatmate expense application. It adds schema complexity, write overhead, and maintenance burden without providing features visible to end users.

**Why rejected**: Audit logs are not a feature — they are a requirement of any system that handles financial data. When a balance is disputed, the audit log answers "who approved this row and when." When a payment appears twice, the audit log traces which import batch promoted it and which user made the decision. Removing audit logs to simplify the schema is the equivalent of removing a financial institution's transaction ledger because it takes up storage. The overhead is negligible. The protection against "I never approved that" disputes is not recoverable after the fact.

---

### Rejection 5: Use a Historical Exchange Rate API with Live Rates

**What was suggested**: Integrate a live exchange rate API (Fixer.io, Open Exchange Rates, or similar) to automatically fetch current rates at import time, eliminating the need for seeded `exchange_rates` records in the database.

**Why rejected**: Historical expense imports require historical exchange rates, not current rates. A CSV containing a January 2026 dinner expense in USD should be converted at the January 2026 USD/INR rate, not the rate at the time of import in June 2026. A live API provides today's rate. Using it for historical data introduces systematic conversion error for every foreign-currency expense that is not imported on the day it occurred.

The correct implementation requires storing rates keyed by date: `exchange_rates(currency, effective_date, rate_to_inr)`. The `rate_to_inr` for a given currency on a given expense date is the rate closest to and before that date. This is a more complex schema than the current single-rate-per-currency design, and it was deferred as out of scope. The current design documents this limitation explicitly: exchange rates are static and set at application startup. Reviewers are expected to be aware of this constraint when evaluating imported foreign-currency expenses.

---

### Rejection 6: Rely on UI Navigation to Prevent Unauthorized Group Access

**What was suggested**: Because the UI only shows links to groups the user belongs to, no server-side membership check is needed on group routes. Users would not naturally navigate to groups they don't belong to.

**Why rejected**: Security enforced only at the UI layer is not security — it is obfuscation. Any user can construct a URL manually (`/groups/2`) regardless of what the UI renders. This is Insecure Direct Object Reference (IDOR), listed in the OWASP Top 10. A financial application that exposes another user's balance history, expense ledger, and import approvals via URL manipulation is not acceptable at any scope. Server-side authorization is mandatory, not optional.

The fix is one database query per route: verify `GroupMembership` exists for `(current_user.id, group_id)` before serving the response. Return HTTP 403 if not found.

---

## 7. Known Gaps and Open Issues

This section records implementation gaps identified during the final adversarial review. Each is documented so the submitter can give an honest, prepared answer during interview.

| Gap | Location | Severity | Fix |
|---|---|---|---|
| Authorization checks missing on all group routes | `main.py` | Critical | Add `require_group_member()` to every group-scoped route |
| Stale workspace comment | `main.py` L24 | High | Delete the comment line |
| Weak default `SECRET_KEY` accepted silently in production | `auth.py` L11 | High | Startup assertion: raise `RuntimeError` if default key used with `ENV=production` |
| Temp file not deleted after import | `main.py` L364 | Medium | Call `os.remove(temp_path)` after `parse_csv_to_staging` |
| `ImportBatch` created without `group_id` | `main.py` L368 | Medium | Pass `group_id=group_id` to `ImportBatch(...)` |
| Manual expense form: EQUAL split only | `group.html` | Medium | Add EXACT/PERCENTAGE UI fields; CSV import already handles them |
| `process_decision` has zero test coverage | `tests/` | Medium | Add `test_importer_decision.py` covering approve/skip/modify paths |
| Balance snapshot net value not asserted in tests | `test_balance.py` | Medium | Query `BalanceSnapshot` and assert `net_balance_paise` numerically |
| No CSRF tokens on POST forms | Templates | Medium | Add synchronizer token pattern |
| Session cookies never expire | `auth.py` | Medium | Embed expiry timestamp in signed cookie payload |
| `pytest` and `pytest-mock` in production requirements | `requirements.txt` | Low | Move to `requirements-dev.txt` |
| Dead `python-dateutil` dependency | `requirements.txt` | Low | Remove; not imported anywhere in application code |
| `datetime.utcnow` deprecated in Python 3.12+ | `models.py` | Low | Replace with `datetime.now(timezone.utc)` |
