import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# Force in-memory SQLite for test runs and override app.database variables
from app import database
database.DATABASE_URL = "sqlite:///:memory:"
database.engine = create_engine(database.DATABASE_URL, connect_args={"check_same_thread": False})
database.SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=database.engine)

import pytest
from decimal import Decimal
from app.database import engine, Base, SessionLocal
from app.models import User, Group, GroupMembership, Expense, ExpenseShare, Payment
from app.balance import recalculate_snapshots, explain_balance_trace, simplify_debts
import datetime

@pytest.fixture(scope="module")
def balance_db():
    Base.metadata.create_all(bind=engine)
    
    db = SessionLocal()
    
    # 1. Create users
    aisha = User(username="Aisha", password_hash="hash")
    rohan = User(username="Rohan", password_hash="hash")
    priya = User(username="Priya", password_hash="hash")
    sam = User(username="Sam", password_hash="hash")
    db.add_all([aisha, rohan, priya, sam])
    db.commit()

    # 2. Create group
    g = Group(name="Flat 204")
    db.add(g)
    db.commit()
    db.refresh(g)

    # 3. Create memberships with varying tenures
    db.add(GroupMembership(group_id=g.id, user_id=aisha.id, joined_date=datetime.date(2026, 1, 1)))
    db.add(GroupMembership(group_id=g.id, user_id=rohan.id, joined_date=datetime.date(2026, 1, 1)))
    db.add(GroupMembership(group_id=g.id, user_id=priya.id, joined_date=datetime.date(2026, 1, 1)))
    db.add(GroupMembership(group_id=g.id, user_id=sam.id, joined_date=datetime.date(2026, 3, 1))) # Sam joins Mar 1
    db.commit()

    yield db, g, aisha, rohan, priya, sam
    db.close()
    Base.metadata.drop_all(bind=engine)

def test_balance_snapshots_and_trace(balance_db):
    db, group, aisha, rohan, priya, sam = balance_db

    # Jan 15: Rent expense of ₹3000 paid by Aisha. Shared equally among active members (Aisha, Rohan, Priya)
    # Sam is NOT in the flat yet, so he should not owe anything!
    exp1 = Expense(
        group_id=group.id,
        description="January Rent",
        original_amount_paise=300000,
        original_currency="INR",
        exchange_rate=Decimal("1.0"),
        converted_amount_paise=300000,
        date=datetime.date(2026, 1, 15),
        paid_by_id=aisha.id,
        split_type="EQUAL"
    )
    db.add(exp1)
    db.commit()
    db.refresh(exp1)

    db.add(ExpenseShare(expense_id=exp1.id, user_id=aisha.id, share_amount_paise=100000))
    db.add(ExpenseShare(expense_id=exp1.id, user_id=rohan.id, share_amount_paise=100000))
    db.add(ExpenseShare(expense_id=exp1.id, user_id=priya.id, share_amount_paise=100000))
    db.commit()

    # Recalculate snapshots
    recalculate_snapshots(group.id, db)

    # Verify balances: Aisha paid 3000, owes 1000 -> Net: +2000
    # Rohan owes 1000 -> Net: -1000
    # Priya owes 1000 -> Net: -1000
    # Sam -> Net: 0 (not active)
    aisha_snap = db.query(User).filter(User.username == "Aisha").first().expense_shares
    assert len(aisha_snap) == 1

    # Verify chronological trace
    trace = explain_balance_trace(rohan.id, group.id, db)
    assert len(trace) == 1
    assert trace[0]["running_balance_paise"] == -100000

def test_debt_simplification(balance_db):
    db, group, aisha, rohan, priya, sam = balance_db
    
    # Run simplification solver
    # Aisha: +2000, Rohan: -1000, Priya: -1000
    instructions = simplify_debts(group.id, db)
    
    # Should result in:
    # 1. Rohan pays Aisha ₹1000
    # 2. Priya pays Aisha ₹1000
    assert len(instructions) == 2
    
    inst_from_users = {i["from_username"] for i in instructions}
    assert "Rohan" in inst_from_users
    assert "Priya" in inst_from_users
    
    for inst in instructions:
        assert inst["to_username"] == "Aisha"
        assert inst["amount_paise"] == 100000
