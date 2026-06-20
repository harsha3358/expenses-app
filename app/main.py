import os
import datetime
from decimal import Decimal
from fastapi import FastAPI, Depends, Request, Form, File, UploadFile, HTTPException, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.database import engine, get_db, init_db, SessionLocal
from app.models import (
    User, Group, GroupMembership, Expense, ExpenseShare, Payment,
    ImportBatch, StagedExpense, StagedExpenseShare, StagedAnomaly,
    ImportDecision, ExchangeRate, BalanceSnapshot, AuditLog
)
from app.auth import get_current_user, hash_password, verify_password, sign_user_id
from app.importer import parse_csv_to_staging, process_decision, parse_date, parse_amount
from app.balance import recalculate_snapshots, explain_balance_trace, simplify_debts

import secrets
import hmac

# Create the application
app = FastAPI(title="Shared Expenses Manager")

# Mount static files and templates
# Note: Cwd is within the project workspace C:\Users\harsh\.gemini\antigravity-ide\scratch\shared-expenses-app
os.makedirs("static/css", exist_ok=True)
app.mount("/static", StaticFiles(directory="static"), name="static")

templates = Jinja2Templates(directory="app/templates")

def seed_database(db: Session):
    """
    Seeds default exchange rates, users, group, and memberships for verification.
    Password for all seeded accounts is 'flatmate123'.
    """
    # 1. Seed exchange rates
    rates = {
        "INR": Decimal("1.000000"),
        "USD": Decimal("83.000000"),
        "EUR": Decimal("90.000000")
    }
    for curr, rate in rates.items():
        existing = db.query(ExchangeRate).filter(ExchangeRate.currency == curr).first()
        if not existing:
            db.add(ExchangeRate(currency=curr, rate_to_inr=rate))
    db.commit()

    # 2. Seed default group
    group = db.query(Group).filter(Group.name == "Flat 204").first()
    if not group:
        group = Group(name="Flat 204")
        db.add(group)
        db.commit()
        db.refresh(group)

    # 3. Seed users
    flatmates = [
        # (username, joined_date, left_date)
        ("Aisha", datetime.date(2026, 1, 1), None),
        ("Rohan", datetime.date(2026, 1, 1), None),
        ("Priya", datetime.date(2026, 1, 1), None),
        ("Sam", datetime.date(2026, 3, 1), None),
        ("Meera", datetime.date(2026, 4, 1), datetime.date(2026, 6, 1))
    ]

    hashed = hash_password("flatmate123")
    for name, joined, left in flatmates:
        user = db.query(User).filter(User.username == name).first()
        if not user:
            user = User(username=name, password_hash=hashed)
            db.add(user)
            db.commit()
            db.refresh(user)

        # Check membership
        memb = db.query(GroupMembership).filter(
            GroupMembership.group_id == group.id,
            GroupMembership.user_id == user.id
        ).first()
        if not memb:
            db.add(GroupMembership(
                group_id=group.id,
                user_id=user.id,
                joined_date=joined,
                left_date=left
            ))
    db.commit()
    
    # Run initial balance snapshots recomputation
    recalculate_snapshots(group.id, db)

@app.on_event("startup")
def on_startup():
    init_db()
    db = SessionLocal()
    try:
        seed_database(db)
    finally:
        db.close()

# Context processor for templates
@app.middleware("http")
async def csrf_middleware(request: Request, call_next):
    csrf_token = request.cookies.get("csrf_token")
    if not csrf_token:
        csrf_token = secrets.token_urlsafe(32)
    request.state.csrf_token = csrf_token
    response = await call_next(request)
    if request.cookies.get("csrf_token") != csrf_token:
        response.set_cookie(
            "csrf_token",
            csrf_token,
            httponly=True,
            samesite="lax",
            secure=os.getenv("ENV") == "production"
        )
    return response

async def verify_csrf(request: Request):
    if request.method in ["POST", "PUT", "DELETE", "PATCH"]:
        form = await request.form()
        cookie_token = request.cookies.get("csrf_token")
        form_token = form.get("csrf_token")
        if not cookie_token or not form_token or not hmac.compare_digest(cookie_token, form_token):
            raise HTTPException(status_code=403, detail="Invalid CSRF token")

def require_group_member(group_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if not current_user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    memb = db.query(GroupMembership).filter(
        GroupMembership.group_id == group_id,
        GroupMembership.user_id == current_user.id
    ).first()
    if not memb:
        raise HTTPException(status_code=403, detail="Not a member of this group")
    return current_user

# Routes
@app.get("/", response_class=HTMLResponse)
def get_home(request: Request, current_user: User = Depends(get_current_user)):
    if not current_user:
        return RedirectResponse(url="/login", status_code=303)
    # Redirect to first group
    db = SessionLocal()
    try:
        memb = db.query(GroupMembership).filter(GroupMembership.user_id == current_user.id).first()
        if memb:
            return RedirectResponse(url=f"/groups/{memb.group_id}", status_code=303)
        return HTMLResponse("You are not part of any group.")
    finally:
        db.close()

@app.get("/login", response_class=HTMLResponse)
def get_login(request: Request):
    return templates.TemplateResponse(request, "login.html", {"error": None})

@app.post("/login", dependencies=[Depends(verify_csrf)])
def post_login(
    response: Response,
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db)
):
    user = db.query(User).filter(User.username == username.strip()).first()
    if not user or not verify_password(password, user.password_hash):
        return templates.TemplateResponse(
            request, "login.html", {"error": "Invalid username or password."}
        )
    
    # Create secure signed cookie session redirecting to home
    signed_value = sign_user_id(user.id)
    redir = RedirectResponse(url="/", status_code=303)
    redir.set_cookie(
        key="session_user_id",
        value=signed_value,
        httponly=True,
        samesite="lax",
        secure=os.getenv("ENV") == "production"
    )
    return redir

@app.get("/register", response_class=HTMLResponse)
def get_register(request: Request):
    return templates.TemplateResponse(request, "register.html", {"error": None})

@app.post("/register", dependencies=[Depends(verify_csrf)])
def post_register(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db)
):
    cleaned_name = username.strip()
    if not cleaned_name:
        return templates.TemplateResponse(
            request, "register.html", {"error": "Username cannot be empty."}
        )
    
    existing = db.query(User).filter(User.username == cleaned_name).first()
    if existing:
        return templates.TemplateResponse(
            request, "register.html", {"error": "Username already exists."}
        )
        
    user = User(username=cleaned_name, password_hash=hash_password(password))
    db.add(user)
    db.commit()
    db.refresh(user)

    # Automatically add new user to group ID 1 for evaluation
    group = db.query(Group).first()
    if group:
        db.add(GroupMembership(
            group_id=group.id,
            user_id=user.id,
            joined_date=datetime.date.today()
        ))
        db.commit()

    return RedirectResponse(url="/login", status_code=303)

@app.get("/logout")
def get_logout():
    redir = RedirectResponse(url="/login", status_code=303)
    redir.delete_cookie("session_user_id")
    return redir

@app.get("/groups/{group_id}", response_class=HTMLResponse)
def get_group(
    group_id: int,
    request: Request,
    current_user: User = Depends(require_group_member),
    db: Session = Depends(get_db)
):
    group = db.query(Group).filter(Group.id == group_id).first()
    if not group:
        raise HTTPException(status_code=404, detail="Group not found")

    # Fetch expenses and payments
    expenses = db.query(Expense).filter(Expense.group_id == group_id).order_by(Expense.date.desc()).all()
    payments = db.query(Payment).filter(Payment.group_id == group_id).order_by(Payment.date.desc()).all()
    
    # Fetch balances snapshots and simplify instructions
    snapshots = db.query(BalanceSnapshot).filter(BalanceSnapshot.group_id == group_id).all()
    simplified_debts = simplify_debts(group_id, db)
    
    # Fetch all members of group for expense forms
    memberships = db.query(GroupMembership).filter(GroupMembership.group_id == group_id).all()
    members = [m.user for m in memberships]

    return templates.TemplateResponse(request, "group.html", {
        "group": group,
        "current_user": current_user,
        "expenses": expenses,
        "payments": payments,
        "snapshots": snapshots,
        "simplified_debts": simplified_debts,
        "members": members,
        "memberships": memberships
    })

@app.post("/groups/{group_id}/expenses", dependencies=[Depends(verify_csrf)])
def post_expense(
    group_id: int,
    description: str = Form(...),
    amount_str: str = Form(...),
    currency: str = Form(...),
    date_str: str = Form(...),
    paid_by_id: int = Form(...),
    split_type: str = Form(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_group_member)
):
        
    amount_paise, _ = parse_amount(amount_str)
    parsed_date = parse_date(date_str) or datetime.date.today()
    
    # Perform currency conversion
    exchange_rate = Decimal("1.000000")
    if currency != "INR":
        rate_record = db.query(ExchangeRate).filter(ExchangeRate.currency == currency).first()
        exchange_rate = rate_record.rate_to_inr if rate_record else Decimal("83.000000")
    
    converted_amount_paise = int(round(float(amount_paise) * float(exchange_rate)))

    # Save expense
    exp = Expense(
        group_id=group_id,
        description=description,
        original_amount_paise=amount_paise,
        original_currency=currency,
        exchange_rate=exchange_rate,
        converted_amount_paise=converted_amount_paise,
        date=parsed_date,
        paid_by_id=paid_by_id,
        split_type=split_type
    )
    db.add(exp)
    db.flush()

    # Calculate active members on this date for splits
    active_m = db.query(GroupMembership).filter(
        GroupMembership.group_id == group_id,
        GroupMembership.joined_date <= parsed_date,
        (GroupMembership.left_date == None) | (GroupMembership.left_date >= parsed_date)
    ).all()
    active_uids = [m.user_id for m in active_m]

    if not active_uids:
        # Fallback to all group members if somehow empty
        active_uids = [m.user_id for m in db.query(GroupMembership).filter(GroupMembership.group_id == group_id).all()]

    # Equal split share
    share_paise = converted_amount_paise // len(active_uids)
    remainder = converted_amount_paise % len(active_uids)

    for idx, uid in enumerate(active_uids):
        amt = share_paise
        if idx == 0:
            amt += remainder
        db.add(ExpenseShare(expense_id=exp.id, user_id=uid, share_amount_paise=amt))

    db.add(AuditLog(
        user_id=current_user.id,
        action="CREATE_EXPENSE",
        target_type="expense",
        target_id=exp.id,
        details={"description": description, "amount": converted_amount_paise}
    ))
    db.commit()

    recalculate_snapshots(group_id, db)
    return RedirectResponse(url=f"/groups/{group_id}", status_code=303)

@app.post("/groups/{group_id}/payments", dependencies=[Depends(verify_csrf)])
def post_payment(
    group_id: int,
    from_user_id: int = Form(...),
    to_user_id: int = Form(...),
    amount_str: str = Form(...),
    date_str: str = Form(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_group_member)
):
        
    amount_paise, _ = parse_amount(amount_str)
    parsed_date = parse_date(date_str) or datetime.date.today()

    pm = Payment(
        group_id=group_id,
        from_user_id=from_user_id,
        to_user_id=to_user_id,
        amount_paise=amount_paise,
        date=parsed_date,
        notes="Manual cash settlement entry."
    )
    db.add(pm)
    db.flush()

    db.add(AuditLog(
        user_id=current_user.id,
        action="CREATE_SETTLEMENT",
        target_type="payment",
        target_id=pm.id,
        details={"amount": amount_paise, "from_user_id": from_user_id, "to_user_id": to_user_id}
    ))
    db.commit()

    recalculate_snapshots(group_id, db)
    return RedirectResponse(url=f"/groups/{group_id}", status_code=303)

@app.post("/groups/{group_id}/import", dependencies=[Depends(verify_csrf)])
def post_import_csv(
    group_id: int,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_group_member)
):
        
    # Write file locally inside /scratch directory
    temp_dir = "scratch/temp"
    os.makedirs(temp_dir, exist_ok=True)
    temp_path = os.path.join(temp_dir, file.filename)
    
    with open(temp_path, "wb") as f:
        f.write(file.file.read())

    # Create batch record
    batch = ImportBatch(filename=file.filename, status="PENDING", group_id=group_id)
    db.add(batch)
    db.commit()
    db.refresh(batch)

    # Ingest rows to staged tables
    parse_csv_to_staging(temp_path, batch.id, group_id, db)
    
    # Cleanup temp file
    if os.path.exists(temp_path):
        os.remove(temp_path)

    db.add(AuditLog(
        user_id=current_user.id,
        action="UPLOAD_CSV",
        target_type="import_batch",
        target_id=batch.id,
        details={"filename": file.filename}
    ))
    db.commit()

    return RedirectResponse(url=f"/import-batches/{batch.id}/report", status_code=303)

@app.get("/import-batches/{batch_id}/report", response_class=HTMLResponse)
def get_import_report(
    batch_id: int,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    if not current_user:
        return RedirectResponse(url="/login", status_code=303)

    batch = db.query(ImportBatch).filter(ImportBatch.id == batch_id).first()
    if not batch:
        raise HTTPException(status_code=404, detail="Batch not found")

    # Group staged expenses into PENDING_APPROVAL vs others
    pending = db.query(StagedExpense).filter(
        StagedExpense.batch_id == batch_id,
        StagedExpense.status == "PENDING_APPROVAL"
    ).order_by(StagedExpense.row_number).all()

    completed = db.query(StagedExpense).filter(
        StagedExpense.batch_id == batch_id,
        StagedExpense.status != "PENDING_APPROVAL"
    ).order_by(StagedExpense.row_number).all()

    # Get users for mapping options
    users = db.query(User).all()
    group_id = db.query(GroupMembership).filter(GroupMembership.user_id == current_user.id).first().group_id

    return templates.TemplateResponse(request, "import_report.html", {
        "batch": batch,
        "pending": pending,
        "completed": completed,
        "users": users,
        "group_id": group_id
    })

@app.post("/staged-expenses/{staged_id}/decide", dependencies=[Depends(verify_csrf)])
def post_decide(
    staged_id: int,
    decision: str = Form(...), # 'APPROVED' / 'SKIPPED' / 'MODIFIED'
    description: str = Form(None),
    raw_amount: str = Form(None),
    raw_date: str = Form(None),
    paid_by_id: int = Form(None),
    split_type: str = Form(None),
    notes: str = Form(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    if not current_user:
        raise HTTPException(status_code=401)

    modifications = {
        "notes": notes or ""
    }
    if description is not None:
        modifications["description"] = description
    if raw_amount is not None:
        modifications["raw_amount"] = raw_amount
    if raw_date is not None:
        modifications["date"] = raw_date
    if paid_by_id is not None:
        modifications["paid_by_id"] = paid_by_id
    if split_type is not None:
        modifications["split_type"] = split_type

    status, promoted_id = process_decision(staged_id, decision, current_user.id, modifications, db)
    
    # Recalculate group balance snapshots
    staged = db.query(StagedExpense).filter(StagedExpense.id == staged_id).first()
    if staged:
        group_memb = db.query(GroupMembership).filter(GroupMembership.user_id == current_user.id).first()
        if group_memb:
            recalculate_snapshots(group_memb.group_id, db)

        # If all staged expenses in the batch are resolved, mark batch as COMPLETED
        batch_id = staged.batch_id
        rem = db.query(StagedExpense).filter(
            StagedExpense.batch_id == batch_id,
            StagedExpense.status == "PENDING_APPROVAL"
        ).count()
        if rem == 0:
            batch = db.query(ImportBatch).filter(ImportBatch.id == batch_id).first()
            if batch:
                batch.status = "COMPLETED"
                db.commit()

        return RedirectResponse(url=f"/import-batches/{staged.batch_id}/report", status_code=303)

    return RedirectResponse(url="/", status_code=303)

@app.get("/groups/{group_id}/balances/trace/{user_id}", response_class=HTMLResponse)
def get_balance_trace(
    group_id: int,
    user_id: int,
    request: Request,
    current_user: User = Depends(require_group_member),
    db: Session = Depends(get_db)
):

    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
        
    trace_events = explain_balance_trace(user_id, group_id, db)
    snapshot = db.query(BalanceSnapshot).filter(
        BalanceSnapshot.group_id == group_id,
        BalanceSnapshot.user_id == user_id
    ).first()

    return templates.TemplateResponse(request, "group.html", {
        "group": db.query(Group).filter(Group.id == group_id).first(),
        "current_user": current_user,
        "trace_user": user,
        "trace_events": trace_events,
        "trace_snapshot": snapshot,
        # Default keys to prevent template render issues
        "expenses": db.query(Expense).filter(Expense.group_id == group_id).order_by(Expense.date.desc()).all(),
        "payments": db.query(Payment).filter(Payment.group_id == group_id).order_by(Payment.date.desc()).all(),
        "snapshots": db.query(BalanceSnapshot).filter(BalanceSnapshot.group_id == group_id).all(),
        "simplified_debts": simplify_debts(group_id, db),
        "members": [m.user for m in db.query(GroupMembership).filter(GroupMembership.group_id == group_id).all()],
        "memberships": db.query(GroupMembership).filter(GroupMembership.group_id == group_id).all()
    })
