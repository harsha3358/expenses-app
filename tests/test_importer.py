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
from app.models import User, Group, GroupMembership, ExchangeRate, StagedExpense, StagedAnomaly
from app.importer import (
    normalize_name, parse_amount, parse_date, detect_anomalies_for_row
)
import datetime

@pytest.fixture(scope="module")
def test_db():
    Base.metadata.create_all(bind=engine)
    
    db = SessionLocal()
    
    # Seed standard test users
    u1 = User(username="Aisha", password_hash="hash")
    u2 = User(username="Rohan", password_hash="hash")
    u3 = User(username="Priya", password_hash="hash")
    u4 = User(username="Sam", password_hash="hash")
    u5 = User(username="Meera", password_hash="hash")
    db.add_all([u1, u2, u3, u4, u5])
    db.commit()

    # Seed group
    g = Group(name="Flat 204")
    db.add(g)
    db.commit()
    db.refresh(g)

    # Seed memberships
    db.add(GroupMembership(group_id=g.id, user_id=u1.id, joined_date=datetime.date(2026, 1, 1)))
    db.add(GroupMembership(group_id=g.id, user_id=u2.id, joined_date=datetime.date(2026, 1, 1)))
    db.add(GroupMembership(group_id=g.id, user_id=u3.id, joined_date=datetime.date(2026, 1, 1)))
    db.add(GroupMembership(group_id=g.id, user_id=u4.id, joined_date=datetime.date(2026, 3, 1)))
    db.add(GroupMembership(group_id=g.id, user_id=u5.id, joined_date=datetime.date(2026, 4, 1), left_date=datetime.date(2026, 6, 1)))
    
    # Seed exchange rates
    db.add(ExchangeRate(currency="USD", rate_to_inr=Decimal("83.00")))
    db.add(ExchangeRate(currency="EUR", rate_to_inr=Decimal("90.00")))
    db.add(ExchangeRate(currency="INR", rate_to_inr=Decimal("1.00")))
    db.commit()

    yield db
    db.close()
    Base.metadata.drop_all(bind=engine)

def test_normalize_name():
    assert normalize_name("aisha") == "Aisha"
    assert normalize_name("Aisha M.") == "Aisha"
    assert normalize_name("  rohan s.  ") == "Rohan"
    assert normalize_name("Random Guy") == "Random Guy"

def test_parse_amount():
    # INR Default
    amt, curr = parse_amount("1200")
    assert amt == 120000 and curr == "INR"

    # Symbols Strip
    amt, curr = parse_amount("₹4500")
    assert amt == 450000 and curr == "INR"

    # USD Conversion Key
    amt, curr = parse_amount("$120.50")
    assert amt == 12050 and curr == "USD"

    # Negative Amounts (Refund candidates)
    amt, curr = parse_amount("-₹500.00")
    assert amt == -50000 and curr == "INR"

def test_parse_date():
    assert parse_date("2026-06-15") == datetime.date(2026, 6, 15)
    assert parse_date("15/06/2026") == datetime.date(2026, 6, 15)
    assert parse_date("June 15, 2026") == datetime.date(2026, 6, 15)
    assert parse_date("not-a-date") is None

def test_anomaly_detections(test_db):
    group = test_db.query(Group).filter(Group.name == "Flat 204").first()
    
    # Test Anomaly: Duplicate and normal row behavior
    row_data_clean = {
        "Date": "2026-01-05",
        "Description": "Electricity Bill",
        "Amount": "₹4500",
        "Currency": "INR",
        "Paid By": "Aisha",
        "Split Type": "EQUAL",
        "Split Details": "",
        "Category": "Utilities"
    }

    # Should detect currency symbol
    staged, anomalies, shares = detect_anomalies_for_row(row_data_clean, 1, group.id, test_db, 1)
    
    anomaly_types = [a["anomaly_type"] for a in anomalies]
    assert "CURRENCY_SYMBOL_IN_AMOUNT" in anomaly_types
    # Verify shares divided equally among active members (Aisha, Rohan, Priya active in Jan)
    assert len(shares) == 3

    # Test Anomaly: USD conversion
    row_usd = {
        "Date": "2026-02-15",
        "Description": "Dinner out",
        "Amount": "$120.50",
        "Currency": "USD",
        "Paid By": "Priya",
        "Split Type": "EQUAL",
        "Split Details": "",
        "Category": "Food"
    }
    staged_usd, anomalies_usd, shares_usd = detect_anomalies_for_row(row_usd, 2, group.id, test_db, 1)
    anomaly_types_usd = [a["anomaly_type"] for a in anomalies_usd]
    assert "FOREIGN_CURRENCY" in anomaly_types_usd
    assert "CURRENCY_SYMBOL_IN_AMOUNT" in anomaly_types_usd

    # Test Anomaly: Payer not active on date (Sam joined Mar 1, expense in Feb)
    row_sam_early = {
        "Date": "2026-02-28",
        "Description": "Internet",
        "Amount": "1500",
        "Currency": "INR",
        "Paid By": "Sam",
        "Split Type": "EQUAL",
        "Split Details": "",
        "Category": "Utilities"
    }
    staged_sam, anomalies_sam, shares_sam = detect_anomalies_for_row(row_sam_early, 3, group.id, test_db, 1)
    anomaly_types_sam = [a["anomaly_type"] for a in anomalies_sam]
    assert "PAYER_OUTSIDE_TENURE" in anomaly_types_sam
