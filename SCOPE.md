# Scope & Specifications Document

This document records the data models, entity relationships, varying tenure validations, balance workflow, import pipeline, and complete anomaly matrix for the Shared Expenses application.


---

## 1. Database Entity Relationship Diagram (ERD)

```mermaid
erDiagram
    users ||--o{ group_memberships : has
    groups ||--o{ group_memberships : has
    groups ||--o{ expenses : tracks
    groups ||--o{ payments : tracks
    groups ||--o{ balance_snapshots : caches
    users ||--o{ expenses : pays
    expenses ||--o{ expense_shares : splits
    users ||--o{ expense_shares : owes
    users ||--o{ payments : sends
    users ||--o{ payments : receives
    import_batches ||--o{ staged_expenses : includes
    staged_expenses ||--o{ staged_expense_shares : splits
    staged_expenses ||--o{ staged_anomalies : flags
    staged_anomalies ||--o{ import_decisions : resolved_by
    users ||--o{ import_decisions : decides
    users ||--o{ audit_logs : performs
```

---

## 2. Table Schemas (PostgreSQL Types)

- **`users`**: `id` (SERIAL PK), `username` (VARCHAR 100 UNIQUE NOT NULL), `password_hash` (VARCHAR 255 NOT NULL)
- **`groups`**: `id` (SERIAL PK), `name` (VARCHAR 255 NOT NULL)
- **`group_memberships`**: `id` (SERIAL PK), `group_id` (FK → groups), `user_id` (FK → users), `joined_date` (DATE NOT NULL), `left_date` (DATE NULLABLE). UNIQUE(`group_id`, `user_id`).
- **`expenses`**: `id` (SERIAL PK), `group_id` (FK), `description` (VARCHAR 255), `original_amount_paise` (INTEGER), `original_currency` (VARCHAR 10), `exchange_rate` (NUMERIC 12,6), `converted_amount_paise` (INTEGER), `date` (DATE), `paid_by_id` (FK), `split_type` (VARCHAR 50), `category` (VARCHAR 100), `import_batch_id` (FK NULLABLE).
- **`expense_shares`**: `id` (SERIAL PK), `expense_id` (FK), `user_id` (FK), `share_amount_paise` (INTEGER).
- **`payments`**: `id` (SERIAL PK), `group_id` (FK), `from_user_id` (FK), `to_user_id` (FK), `amount_paise` (INTEGER), `date` (DATE), `notes` (TEXT).
- **`balance_snapshots`**: `id` (SERIAL PK), `group_id` (FK), `user_id` (FK), `calculated_at` (TIMESTAMP), `net_balance_paise` (INTEGER), `paid_amt_paise` (INTEGER), `owed_amt_paise` (INTEGER), `settlements_sent_paise` (INTEGER), `settlements_received_paise` (INTEGER). UNIQUE(`group_id`, `user_id`).
- **`import_batches`**: `id` (SERIAL PK), `group_id` (FK → groups NULLABLE), `filename` (VARCHAR 255), `status` (VARCHAR 50), `imported_at` (TIMESTAMP).
- **`staged_expenses`**: `id` (SERIAL PK), `batch_id` (FK), `row_number` (INTEGER), `description` (VARCHAR), `raw_amount` (VARCHAR), `raw_currency` (VARCHAR), `raw_date` (VARCHAR), `raw_paid_by` (VARCHAR), `raw_split_type` (VARCHAR), `raw_split_details` (TEXT), `category` (VARCHAR), `status` (VARCHAR).
- **`staged_anomalies`**: `id` (SERIAL PK), `staged_expense_id` (FK), `anomaly_type` (VARCHAR 50), `severity` (VARCHAR 20), `description` (TEXT), `proposed_correction` (TEXT).
- **`import_decisions`**: `id` (SERIAL PK), `anomaly_id` (FK), `decision` (VARCHAR 50), `decision_by` (FK), `decision_time` (TIMESTAMP), `notes` (TEXT).
- **`audit_logs`**: `id` (SERIAL PK), `timestamp` (TIMESTAMP), `user_id` (FK), `action` (VARCHAR 100), `target_type` (VARCHAR 50), `target_id` (INTEGER), `details` (JSON).

---

## 3. Varying Tenure & Dynamic Splits Rules
Flatmates have active occupancy date windows:
1. When splitting an expense, active members are queried:
   $$\text{ActiveMembers} = \{ U \in \text{Group} \mid \text{joined\_date} \le \text{expense.date} \land (\text{left\_date} \text{ IS NULL} \lor \text{left\_date} \ge \text{expense.date}) \}$$
2. Expenses split only among the active members on that date.
3. If the CSV specifies inactive members, an anomaly `SPLIT_MEMBER_OUTSIDE_TENURE` (Warning) is flagged. If approved, the system redistributes the share only to active members.

---

## 4. Anomaly Matrix & Catalog

The validation engine detects the following anomalies:

- **A01: Duplicate Row (Warning)**: Row matches existing date, paise, payer, and desc. *Policy: Propose skipping.*
- **A02: Missing Currency (Info)**: No currency symbol or value. *Policy: Auto-default to INR.*
- **A03: Foreign Currency (Info)**: Non-INR currency. *Policy: Convert to paise at database exchange rate.*
- **A04: Currency Symbol in Amount (Info)**: Non-numeric symbols (₹, $, Rs) in amount. *Policy: Clean and parse.*
- **A05: Inconsistent Casing/Spaces (Info)**: Spaces or casings in payer name. *Policy: Trim and map to User.*
- **A06: Payer Not Registered (Hard Error)**: Payer is not in the system. *Policy: Block commit until mapped to user.*
- **A07: Payer Outside Tenure (Hard Error)**: Payer is not active on transaction date. *Policy: Block commit.*
- **A08: Split Member Outside Tenure (Warning)**: Split list has inactive user. *Policy: Propose excluding and redistributing.*
- **A09: Percentage Split Total Error (Warning)**: Split percents != 100%. *Policy: Propose rescaling.*
- **A10: Exact Split Total Error (Warning)**: Split sums != total. *Policy: Propose allocating delta to payer.*
- **A11: Negative/Zero Amount (Warning/Error)**: Negative: treat as **Refund Candidate**. Zero: hard error.
- **A12: Malformed Date (Warning/Error)**: Unstandardized date text. *Policy: Parse fallbacks. Fail if unparseable.*
- **A13: Settlement in Expense Sheet (Warning)**: Suggests peer payment. *Policy: Propose promoting to Payment.*
- **A14: Empty Description (Info)**: Description is blank. *Policy: Auto-default to "Imported Expense <Category>".*
- **A15: Future Date (Warning)**: Transaction date is in future. *Policy: Flag warning, import if user confirms.*
- **A16: Outlier Amount (Warning)**: Expense exceeds ₹100,000. *Policy: Flag warning for manual review.*

---

## 5. Balance Workflow

### 5.1 Net Balance Formula

For each group member, the net balance is computed as:

```
net_balance = (paid_amt + sent_amt) - (owed_amt + recv_amt)
```

Where:
- `paid_amt` — total cash the user paid for group expenses (group owes them this back)
- `owed_amt` — the user's share of all group expenses (user owes this for their consumption)
- `sent_amt` — settlement cash the user **sent** to others (paying off own debt → improves net position)
- `recv_amt` — settlement cash the user **received** from others (collecting debt owed → reduces remaining claim)

**Group invariant**: The sum of `net_balance_paise` across all members of a group is always zero. This follows from the double-entry structure:
- Every expense creates shares that sum to exactly the expense amount → paid − owed sums to zero across all members.
- Every settlement payment adds to one user's `sent_amt` and another's `recv_amt` by the same amount → sent − received sums to zero across all members.

### 5.2 Snapshot Trigger Points

Balance snapshots are recomputed immediately after every ledger mutation:

| Event | Route | Snapshot trigger |
|---|---|---|
| Manual expense created | `POST /groups/{id}/expenses` | `recalculate_snapshots(group_id, db)` |
| Settlement payment recorded | `POST /groups/{id}/payments` | `recalculate_snapshots(group_id, db)` |
| Staged CSV row approved | `POST /staged-expenses/{id}/decide` | `recalculate_snapshots(group_id, db)` |
| Application startup | `on_startup` | `recalculate_snapshots(group.id, db)` |

### 5.3 Debt Simplification

The `simplify_debts` function uses a greedy net-balance algorithm:
1. Compute each member's net balance.
2. Separate into creditors (net > 0) and debtors (net < 0).
3. Match the largest creditor to the largest debtor.
4. Record a payment instruction for `min(creditor_balance, debtor_balance)`.
5. Reduce both parties by that amount. Repeat until all balances are zero.

For N unbalanced participants, this produces at most N−1 transactions — proven optimal by induction.

---

## 6. Import Workflow

```
Upload CSV
    ↓
Create ImportBatch (status: PENDING, group_id)
    ↓
parse_csv_to_staging()
    For each row:
        normalize_name(paid_by)
        parse_amount(amount)
        parse_date(date)
        detect_anomalies_for_row()  ← 16 anomaly checks
        Write StagedExpense + StagedAnomalies
    ↓
Import Report displayed
    Rows with anomalies: PENDING_APPROVAL
    Rows without anomalies: auto-staged
    ↓
User reviews each PENDING row:
    APPROVE  → process_decision() promotes to expenses + expense_shares
    SKIP     → staged_expense.status = SKIPPED
    MODIFY   → user edits fields → process_decision() promotes modified version
    ↓
For each promotion:
    Write ImportDecision (who decided, when, notes)
    Write AuditLog (IMPORT_DECISION)
    Trigger recalculate_snapshots()
    ↓
All rows resolved → ImportBatch.status = COMPLETED
```

**Date parsing policy**: Ambiguous two-segment dates (e.g., `05/06/2026`) are interpreted as DD/MM/YYYY. This is consistent with the Indian date format used by the Flat 204 members. Reviewers should verify dates on the import report before approving.

**Exchange rate policy**: Rates are seeded at application startup (INR=1.0, USD=83.0, EUR=90.0). Historical rate lookup by expense date is not implemented. The exchange rate used for each conversion is stored in the `expenses.exchange_rate` column for full traceability.
