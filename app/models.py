import datetime
from sqlalchemy import (
    Column, Integer, String, Date, DateTime, Numeric, 
    ForeignKey, Text, JSON, Boolean, UniqueConstraint
)
from sqlalchemy.orm import relationship
from app.database import Base

class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(100), unique=True, nullable=False, index=True)
    password_hash = Column(String(255), nullable=False)

    memberships = relationship("GroupMembership", back_populates="user", cascade="all, delete-orphan")
    expenses_paid = relationship("Expense", back_populates="paid_by")
    expense_shares = relationship("ExpenseShare", back_populates="user")
    payments_sent = relationship("Payment", foreign_keys="Payment.from_user_id", back_populates="from_user")
    payments_received = relationship("Payment", foreign_keys="Payment.to_user_id", back_populates="to_user")

class Group(Base):
    __tablename__ = "groups"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), nullable=False)

    memberships = relationship("GroupMembership", back_populates="group", cascade="all, delete-orphan")
    expenses = relationship("Expense", back_populates="group", cascade="all, delete-orphan")
    payments = relationship("Payment", back_populates="group", cascade="all, delete-orphan")
    snapshots = relationship("BalanceSnapshot", back_populates="group", cascade="all, delete-orphan")

class GroupMembership(Base):
    __tablename__ = "group_memberships"

    id = Column(Integer, primary_key=True, index=True)
    group_id = Column(Integer, ForeignKey("groups.id", ondelete="CASCADE"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    joined_date = Column(Date, nullable=False)
    left_date = Column(Date, nullable=True)

    group = relationship("Group", back_populates="memberships")
    user = relationship("User", back_populates="memberships")

class ImportBatch(Base):
    __tablename__ = "import_batches"

    id = Column(Integer, primary_key=True, index=True)
    imported_at = Column(DateTime, default=datetime.datetime.utcnow, nullable=False)
    filename = Column(String(255), nullable=False)
    status = Column(String(50), nullable=False) # 'PENDING', 'COMPLETED'

    staged_expenses = relationship("StagedExpense", back_populates="batch", cascade="all, delete-orphan")

class Expense(Base):
    __tablename__ = "expenses"

    id = Column(Integer, primary_key=True, index=True)
    group_id = Column(Integer, ForeignKey("groups.id", ondelete="CASCADE"), nullable=False)
    description = Column(String(255), nullable=False)
    original_amount_paise = Column(Integer, nullable=False)
    original_currency = Column(String(10), nullable=False)
    exchange_rate = Column(Numeric(12, 6), nullable=False)
    converted_amount_paise = Column(Integer, nullable=False) # Converted value in base currency (INR paise)
    date = Column(Date, nullable=False)
    paid_by_id = Column(Integer, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    split_type = Column(String(50), nullable=False) # 'EQUAL', 'EXACT', 'PERCENT'
    category = Column(String(100), nullable=True)
    notes = Column(Text, nullable=True)
    import_batch_id = Column(Integer, ForeignKey("import_batches.id", ondelete="SET NULL"), nullable=True)

    group = relationship("Group", back_populates="expenses")
    paid_by = relationship("User", back_populates="expenses_paid")
    shares = relationship("ExpenseShare", back_populates="expense", cascade="all, delete-orphan")

class ExpenseShare(Base):
    __tablename__ = "expense_shares"

    id = Column(Integer, primary_key=True, index=True)
    expense_id = Column(Integer, ForeignKey("expenses.id", ondelete="CASCADE"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    share_amount_paise = Column(Integer, nullable=False)

    expense = relationship("Expense", back_populates="shares")
    user = relationship("User", back_populates="expense_shares")

class Payment(Base):
    __tablename__ = "payments"

    id = Column(Integer, primary_key=True, index=True)
    group_id = Column(Integer, ForeignKey("groups.id", ondelete="CASCADE"), nullable=False)
    from_user_id = Column(Integer, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    to_user_id = Column(Integer, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    amount_paise = Column(Integer, nullable=False)
    date = Column(Date, nullable=False)
    notes = Column(Text, nullable=True)
    import_batch_id = Column(Integer, ForeignKey("import_batches.id", ondelete="SET NULL"), nullable=True)

    group = relationship("Group", back_populates="payments")
    from_user = relationship("User", foreign_keys=[from_user_id], back_populates="payments_sent")
    to_user = relationship("User", foreign_keys=[to_user_id], back_populates="payments_received")

class StagedExpense(Base):
    __tablename__ = "staged_expenses"

    id = Column(Integer, primary_key=True, index=True)
    batch_id = Column(Integer, ForeignKey("import_batches.id", ondelete="CASCADE"), nullable=False)
    row_number = Column(Integer, nullable=False)
    description = Column(String(255), nullable=True)
    raw_amount = Column(String(50), nullable=True)
    raw_currency = Column(String(10), nullable=True)
    raw_date = Column(String(50), nullable=True)
    raw_paid_by = Column(String(100), nullable=True)
    raw_split_type = Column(String(50), nullable=True)
    raw_split_details = Column(Text, nullable=True) # JSON string representation of target names / split values
    category = Column(String(100), nullable=True)
    status = Column(String(50), nullable=False) # 'PENDING_APPROVAL', 'APPROVED', 'REJECTED', 'MODIFIED'

    batch = relationship("ImportBatch", back_populates="staged_expenses")
    anomalies = relationship("StagedAnomaly", back_populates="staged_expense", cascade="all, delete-orphan")
    shares = relationship("StagedExpenseShare", back_populates="staged_expense", cascade="all, delete-orphan")

class StagedExpenseShare(Base):
    __tablename__ = "staged_expense_shares"

    id = Column(Integer, primary_key=True, index=True)
    staged_expense_id = Column(Integer, ForeignKey("staged_expenses.id", ondelete="CASCADE"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    share_amount_paise = Column(Integer, nullable=False)

    staged_expense = relationship("StagedExpense", back_populates="shares")
    user = relationship("User")

class StagedAnomaly(Base):
    __tablename__ = "staged_anomalies"

    id = Column(Integer, primary_key=True, index=True)
    staged_expense_id = Column(Integer, ForeignKey("staged_expenses.id", ondelete="CASCADE"), nullable=False)
    anomaly_type = Column(String(50), nullable=False) # E.g., 'FOREIGN_CURRENCY', 'DUPLICATE_ROW'
    severity = Column(String(20), nullable=False) # 'ERROR', 'WARNING', 'INFO'
    description = Column(Text, nullable=False)
    proposed_correction = Column(Text, nullable=False)

    staged_expense = relationship("StagedExpense", back_populates="anomalies")
    decisions = relationship("ImportDecision", back_populates="anomaly", cascade="all, delete-orphan")

class ImportDecision(Base):
    __tablename__ = "import_decisions"

    id = Column(Integer, primary_key=True, index=True)
    anomaly_id = Column(Integer, ForeignKey("staged_anomalies.id", ondelete="CASCADE"), nullable=False)
    decision = Column(String(50), nullable=False) # 'APPROVED_AS_IS', 'SKIPPED', 'MODIFIED'
    decision_by = Column(Integer, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    decision_time = Column(DateTime, default=datetime.datetime.utcnow, nullable=False)
    notes = Column(Text, nullable=True)

    anomaly = relationship("StagedAnomaly", back_populates="decisions")
    user = relationship("User")

class BalanceSnapshot(Base):
    __tablename__ = "balance_snapshots"

    id = Column(Integer, primary_key=True, index=True)
    group_id = Column(Integer, ForeignKey("groups.id", ondelete="CASCADE"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    calculated_at = Column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow, nullable=False)
    net_balance_paise = Column(Integer, nullable=False)
    paid_amt_paise = Column(Integer, nullable=False)
    owed_amt_paise = Column(Integer, nullable=False)
    settlements_sent_paise = Column(Integer, nullable=False)
    settlements_received_paise = Column(Integer, nullable=False)

    __table_args__ = (
        UniqueConstraint("group_id", "user_id", name="uq_group_user"),
    )

    group = relationship("Group", back_populates="snapshots")
    user = relationship("User")

class ExchangeRate(Base):
    __tablename__ = "exchange_rates"

    id = Column(Integer, primary_key=True, index=True)
    currency = Column(String(10), unique=True, nullable=False)
    rate_to_inr = Column(Numeric(12, 6), nullable=False)

class AuditLog(Base):
    __tablename__ = "audit_logs"

    id = Column(Integer, primary_key=True, index=True)
    timestamp = Column(DateTime, default=datetime.datetime.utcnow, nullable=False)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    action = Column(String(100), nullable=False) # 'UPLOAD', 'APPROVE', 'REJECT', 'MODIFY', 'SETTLE'
    target_type = Column(String(50), nullable=False) # 'staged_expense', 'expense', 'payment'
    target_id = Column(Integer, nullable=False)
    details = Column(JSON, nullable=False) # Store previous/new state as JSON dictionary

    user = relationship("User")
