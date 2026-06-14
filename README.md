# Shared Expenses App

## The Problem

Five people share a flat. Every month someone pays the electricity bill, someone else buys groceries, and a third person covers the internet. By the end of the month nobody agrees on who owes what.

It gets worse when you try to import three months of transactions from a spreadsheet. Half the rows have typos in names. Some amounts are in dollars. One row is clearly a duplicate. A few dates don't even make sense.

Now multiply that by the fact that one flatmate moved in mid-month and another left early. The old split percentages are now wrong for those weeks.

No one wants to do this math by hand. And nobody trusts that the math was done correctly.

## The Solution

This app solves exactly that.

You upload your messy spreadsheet. The app reads every row and flags anything suspicious — duplicate entries, unrecognised names, wrong currencies, future dates, impossible amounts. Instead of auto-correcting silently, it shows you each problem and asks what to do. You approve, reject, or fix each one before anything touches the ledger.

Once the data is clean, the app calculates who owes who — accounting for when each person was actually living there. It then simplifies the debts. If Aisha owes Rohan ₹500 and Rohan owes Priya ₹500, instead of two payments you get one: Aisha pays Priya directly.

Every balance comes with a full transaction-by-transaction breakdown so anyone can trace exactly how a number was calculated.

## Who It Is For

Flatmates who share expenses and want to settle fairly without arguments about the maths.

---

## Features

- Upload a CSV and review every suspicious row before it enters the ledger
- Detects 16 anomaly types: typos, wrong currencies, duplicate rows, missing names, bad dates, outlier amounts
- Splits expenses only among flatmates who were actually present on that date
- Converts foreign currencies to INR at seeded exchange rates, preserving original amount and rate for auditability
- Simplifies debts to the minimum number of payments
- Shows a full line-by-line trace of how each person's balance was calculated
- Secure login with HMAC-signed, HttpOnly session cookies
- Full audit log of every ledger mutation and import decision

---

## Tech Stack

- **Backend**: Python, FastAPI
- **Database**: PostgreSQL (hosted on Neon)
- **Frontend**: Jinja2 HTML templates, Vanilla CSS
- **Auth**: HMAC-signed session cookies

---

## Running Locally

### Prerequisites

- Python 3.10 or higher
- A PostgreSQL database (local or Neon cloud)

### Install

```bash
git clone https://github.com/harsha3358/expenses-app.git
cd expenses-app

python -m venv venv
venv\Scripts\activate

pip install -r requirements.txt
```

### Configure

```powershell
# PowerShell
$env:DATABASE_URL="postgresql://postgres:password@localhost:5432/shared_expenses"
$env:SECRET_KEY="your-secret-key"
$env:ENV="production"
```

### Start

```bash
python -m uvicorn app.main:app --reload
```

The app seeds five test users on startup: Aisha, Rohan, Priya, Sam, Meera.
Password for all: `flatmate123`

---

## Running Tests

**Windows (PowerShell)**:
```powershell
$env:PYTHONPATH="C:\expenses_app"
pytest
```

**Linux / macOS**:
```bash
PYTHONPATH=. pytest
```

Tests use an in-memory SQLite database. No PostgreSQL connection needed.

---

## Deploying on Render

1. Create a PostgreSQL database on [Neon](https://neon.tech). Copy the connection string.
2. Go to [Render](https://render.com) and create a new Web Service:
   - Build command: `pip install -r requirements.txt`
   - Start command: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
3. Add environment variables:
   - `DATABASE_URL` — your Neon connection string
   - `SECRET_KEY` — a long random string
   - `ENV` — set to `production`

Alternatively, connect the repo and use the included `render.yaml` for one-click deployment.

---

## Screenshots

*(Group dashboard, anomaly review screen, and balance trace view — to be added.)*

---

## Known Limitations

- **Manual expense form**: Supports EQUAL splits only. EXACT and PERCENTAGE splits are supported through the CSV import workflow.
- **Exchange rates**: Static, seeded at startup. Historical rate lookup by date is not implemented.
- **Session expiry**: Signed cookies do not expire server-side. Re-login is only required after explicit logout or secret key rotation.
- **Single group per deployment**: The application is designed for one flat (one group). Multi-group routing exists but is not fully exposed in the UI.

---

## Security

- Passwords are hashed with bcrypt (native, no passlib wrapper).
- Sessions use HMAC-SHA256 signed cookies with `HttpOnly=True`, `SameSite=Lax`, and `Secure=True` in production.
- All database queries use SQLAlchemy ORM parameterization — no raw SQL string interpolation.
- Set `SECRET_KEY` to a cryptographically random string before deployment. Never use the default.
