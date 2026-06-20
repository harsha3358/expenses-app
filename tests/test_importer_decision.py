import os
import pytest
import datetime
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import database
database.DATABASE_URL = "sqlite:///:memory:"
database.engine = create_engine(database.DATABASE_URL, connect_args={"check_same_thread": False})
database.SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=database.engine)

from app.database import engine, Base, SessionLocal
from app.models import User, Group, GroupMembership, ImportBatch, StagedExpense, StagedAnomaly, Expense, ImportDecision
from app.importer import process_decision

@pytest.fixture(scope="module")
def importer_db():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    
    # Setup user
    u = User(username="Testuser", password_hash="hash")
    db.add(u)
    db.commit()
    
    # Setup group
    g = Group(name="Test Group")
    db.add(g)
    db.commit()
    
    db.add(GroupMembership(group_id=g.id, user_id=u.id, joined_date=datetime.date(2026, 1, 1)))
    db.commit()
    
    yield db, g, u
    db.close()
    Base.metadata.drop_all(bind=engine)

def test_process_decision_skipped(importer_db):
    db, g, u = importer_db
    batch = ImportBatch(filename="test.csv", status="PENDING", group_id=g.id)
    db.add(batch)
    db.commit()
    
    staged = StagedExpense(
        batch_id=batch.id,
        row_number=1,
        status="PENDING_APPROVAL"
    )
    db.add(staged)
    db.commit()
    
    anomaly = StagedAnomaly(staged_expense_id=staged.id, anomaly_type="TEST", severity="INFO", description="test", proposed_correction="none")
    db.add(anomaly)
    db.commit()
    
    status, promoted_id = process_decision(staged.id, "SKIPPED", u.id, {}, db)
    
    assert status == "REJECTED"
    assert promoted_id is None
    
    staged_after = db.query(StagedExpense).filter(StagedExpense.id == staged.id).first()
    assert staged_after.status == "REJECTED"
    
    decision = db.query(ImportDecision).filter(ImportDecision.anomaly_id == anomaly.id).first()
    assert decision is not None
    assert decision.decision == "SKIPPED"

def test_process_decision_approved(importer_db):
    db, g, u = importer_db
    batch = ImportBatch(filename="test.csv", status="PENDING", group_id=g.id)
    db.add(batch)
    db.commit()
    
    staged = StagedExpense(
        batch_id=batch.id,
        row_number=2,
        description="Test Exp",
        raw_amount="100.00",
        raw_currency="INR",
        raw_date="2026-01-01",
        raw_paid_by="Testuser",
        raw_split_type="EQUAL",
        status="PENDING_APPROVAL"
    )
    db.add(staged)
    db.commit()
    
    anomaly = StagedAnomaly(staged_expense_id=staged.id, anomaly_type="TEST", severity="INFO", description="test", proposed_correction="none")
    db.add(anomaly)
    db.commit()
    
    status, promoted_id = process_decision(staged.id, "APPROVED", u.id, {}, db)
    
    assert status == "APPROVED"
    assert promoted_id is not None
    
    exp = db.query(Expense).filter(Expense.id == promoted_id).first()
    assert exp is not None
    assert exp.description == "Test Exp"
    assert exp.original_amount_paise == 10000

def test_process_decision_modified(importer_db):
    db, g, u = importer_db
    batch = ImportBatch(filename="test.csv", status="PENDING", group_id=g.id)
    db.add(batch)
    db.commit()
    
    staged = StagedExpense(
        batch_id=batch.id,
        row_number=3,
        description="Old Desc",
        raw_amount="100.00",
        raw_currency="INR",
        raw_date="2026-01-01",
        raw_paid_by="Testuser",
        raw_split_type="EQUAL",
        status="PENDING_APPROVAL"
    )
    db.add(staged)
    db.commit()
    
    anomaly = StagedAnomaly(staged_expense_id=staged.id, anomaly_type="TEST", severity="INFO", description="test", proposed_correction="none")
    db.add(anomaly)
    db.commit()
    
    status, promoted_id = process_decision(staged.id, "MODIFIED", u.id, {"description": "New Desc"}, db)
    
    assert status == "MODIFIED"
    assert promoted_id is not None
    
    exp = db.query(Expense).filter(Expense.id == promoted_id).first()
    assert exp.description == "New Desc"
    
    staged_after = db.query(StagedExpense).filter(StagedExpense.id == staged.id).first()
    assert staged_after.description == "New Desc"
