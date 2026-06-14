# AI Usage Log & Refactor Diary

This document details the collaboration with AI code assistants during the development of the Shared Expenses application, highlighting specific prompts used and corrections applied to automated generation bugs.

---

## 1. AI Tools Used
- **Gemini 3.5 Flash** (via Antigravity IDE): Used for core logic generation, test suite structuring, and documentation.
- **Claude.ai**: Used during the initial scoping and design turns.

---

## 2. Key Prompts Used
- **Staging Schema design**:
  `"Design a relational database schema that supports an import staging area where CSV rows with warnings or errors are parked. Users must be able to approve, reject, or modify each staged row before promoting it to production."`
- **Paise Precision Math**:
  `"Write a Python function to parse currency amounts from string format (containing formatting, symbols like ₹, $, Rs) and convert them to integer paise. How do you divide this integer paise among a group of flatmates equally, and handle the remainder cleanly?"`
- **Trace Engine**:
  `"Write an explanation query in SQLAlchemy that builds a chronological trace history for a user, showing original amounts, currency keys, conversion rates, roles (payer vs shareholder), and running totals."`

---

## 3. AI Mistakes & Corrections

### Mistake 1: Passlib CryptContext Incompatibility
- *Mistake*: The AI generated code using `passlib.context.CryptContext` with the `bcrypt` hashing scheme for authentication.
- *How Identified*: Running the server threw a module warning and failed to verify hashes on modern Python 3.11/3.12 environments due to deprecated internal references inside `passlib`.
- *How Corrected*: Replaced `passlib` entirely by importing Python's native `bcrypt` package directly, using:
  - `bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt())`
  - `bcrypt.checkpw(password.encode("utf-8"), hashed.encode("utf-8"))`

### Mistake 2: Starlette TemplateResponse Signature Change
- *Mistake*: The AI generated template returns using the old Starlette signature:
  `templates.TemplateResponse("login.html", {"request": request, "error": error})`
- *How Identified*: The web router threw a compiler error: `AttributeError: 'dict' object has no attribute 'get_template'` because Starlette interpreted the dictionary as the template name.
- *How Corrected*: Updated all responses to use the modern Starlette template contract where `request` is passed as the first positional argument:
  `templates.TemplateResponse(request, "login.html", {"error": error})`

### Mistake 3: Module Import Path Issues during Test Runs (`pytest`)
- *Mistake*: The AI generated unit tests imported modules using `from app.database import engine`. When executing `pytest` from the root directory, python threw a `ModuleNotFoundError: No module named 'app'`.
- *How Identified*: Running `pytest` returned 2 collection errors.
- *How Corrected*: Pre-configured the shell run commands to export the project root to `PYTHONPATH` using `$env:PYTHONPATH="C:\expenses_app"`, and adjusted the test modules to override database settings dynamically before importing app libraries:
  ```python
  from app import database
  database.DATABASE_URL = "sqlite:///:memory:"
  ```
