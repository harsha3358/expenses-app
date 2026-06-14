import os
# Force sqlite memory database for isolation verification
os.environ["DATABASE_URL"] = "sqlite:///:memory:"

from app.database import engine, init_db, SessionLocal
from app.models import User, Group, GroupMembership, Expense, ExpenseShare
import datetime

print(">>> Verifying Phase 1 Compilation and Initialization...")

# 1. Initialize DB tables
init_db()
print("[SUCCESS] All database tables created successfully.")

# 2. Test basic operations and relations
db = SessionLocal()
try:
    # Create test users
    u1 = User(username="Aisha", password_hash="hash1")
    u2 = User(username="Rohan", password_hash="hash2")
    db.add_all([u1, u2])
    db.commit()
    db.refresh(u1)
    db.refresh(u2)
    print(f"[SUCCESS] Users created: {u1.username} (ID: {u1.id}), {u2.username} (ID: {u2.id})")

    # Create group
    g = Group(name="Flat 204")
    db.add(g)
    db.commit()
    db.refresh(g)
    print(f"[SUCCESS] Group created: {g.name} (ID: {g.id})")

    # Add memberships
    m1 = GroupMembership(group_id=g.id, user_id=u1.id, joined_date=datetime.date(2026, 1, 1))
    m2 = GroupMembership(group_id=g.id, user_id=u2.id, joined_date=datetime.date(2026, 1, 1))
    db.add_all([m1, m2])
    db.commit()
    print(f"[SUCCESS] Memberships added successfully.")

    # Create expense with shares
    exp = Expense(
        group_id=g.id,
        description="Internet bill",
        original_amount_paise=150000, # ₹1500.00
        original_currency="INR",
        exchange_rate=1.0,
        converted_amount_paise=150000,
        date=datetime.date(2026, 1, 10),
        paid_by_id=u1.id,
        split_type="EQUAL"
    )
    db.add(exp)
    db.commit()
    db.refresh(exp)

    share1 = ExpenseShare(expense_id=exp.id, user_id=u1.id, share_amount_paise=75000)
    share2 = ExpenseShare(expense_id=exp.id, user_id=u2.id, share_amount_paise=75000)
    db.add_all([share1, share2])
    db.commit()
    print(f"[SUCCESS] Expense '{exp.description}' (ID: {exp.id}) and shares committed.")

    # Validate relationships
    assert len(g.memberships) == 2, "Group memberships relation failed"
    assert len(exp.shares) == 2, "Expense shares relation failed"
    print("[SUCCESS] SQLAlchemy Relationships verified successfully.")

finally:
    db.close()

print("\n>>> Phase 1 Verification completed successfully! Database design is solid.")
