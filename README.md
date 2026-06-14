# Shared Expenses Application

A production-ready shared expenses application designed to clean and ingest messy, inconsistent expense data from spreadsheets, resolve flatmate balance disputes, and handle complex varying tenure calendars with financial integrity.

---

## Features
- **Secure Credentials System**: Cookie sessions signed with HMAC-SHA256 and secure flags (`HttpOnly`, `SameSite=Lax`).
- **Stateful CSV Ingestion Staging Area**: Uploaded spreadsheets pass validation checks; anomalies are staged for manual review, edit, approval, or skip before ledger promote (Veto power compliance).
- **Dynamic Tenure Calendar Validation**: Group memberships validate member joined/left dates against transaction dates. Omit inactive flatmates dynamically from splits.
- **Explainable Balance Trace Engine**: Chronological traces mapping transaction dates, currency exchange conversions, and roles (Payer vs Shareholder).
- **Greedy Debt Simplification**: Solves circular debts to generate optimized, minimal settlement instruction links.
- **Precision Calculation Engine**: Processes all currency operations in minor units (integer paise) to eliminate float rounding drift.

---

## Tech Stack & Architecture
- **Framework**: FastAPI (Python 3.10+)
- **ORM & Database**: SQLAlchemy (PostgreSQL engine integration, compatible with Neon Serverless)
- **Frontend Views**: Jinja2 HTML Templates + Vanilla CSS

---

## Setup Instructions

### 1. Prerequisites
- Python 3.10 or higher.
- A running PostgreSQL database (local or a cloud-hosted Neon database URL).

### 2. Local Installation
Clone the repository, navigate to the folder, and set up a virtual environment:
```bash
# Clone
git clone https://github.com/harsha3358/expenses-app.git
cd expenses-app

# Create Virtual Environment
python -m venv venv
venv\Scripts\activate

# Install Dependencies
pip install -r requirements.txt
```

### 3. PostgreSQL Configuration
Configure your PostgreSQL server connection string in your `.env` file or environment:
```bash
# Set environment variable (Linux/macOS)
export DATABASE_URL="postgresql://postgres:password@localhost:5432/shared_expenses"
export SECRET_KEY="your-production-secret-key-signature"
export ENV="production"

# In PowerShell (Windows)
$env:DATABASE_URL="postgresql://postgres:password@localhost:5432/shared_expenses"
$env:SECRET_KEY="your-production-secret-key-signature"
$env:ENV="production"
```

### 4. Database Seeding & Startup
Run uvicorn to initialize and seed the tables:
```bash
# Runs uvicorn, database will be auto-seeded on startup with users:
# Aisha, Rohan, Priya, Sam, Meera (Password for all: 'flatmate123')
python -m uvicorn app.main:app --reload
```

---

## Running Tests
Unit tests use an in-memory SQLite database configuration to run isolated from production database connections:
```bash
# Run pytest with PYTHONPATH set
$env:PYTHONPATH="C:\expenses_app"
pytest
```

---

## Deployment on Render (with Neon DB)
1. Register a PostgreSQL database instance on **Neon** (`https://neon.tech`). Copy the Connection string.
2. Sign in to **Render** (`https://render.com`) and choose **Blueprints** to upload via `render.yaml`, or create a **Web Service**:
   - Environment: `Python`
   - Build Command: `pip install -r requirements.txt`
   - Start Command: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
   - Add Environment Variables:
     - `DATABASE_URL`: Your Neon PostgreSQL Connection string.
     - `SECRET_KEY`: A cryptographically secure random secret key.
     - `ENV`: `production`

---

## Screenshots Placeholder
*(Screenshots of Group Dashboard, Anomaly Review Report, and Rohan's Explained Trace Modal will be added here).*
