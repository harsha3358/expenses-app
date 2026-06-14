import csv
import json
import datetime
from decimal import Decimal
from sqlalchemy.orm import Session
from app.models import (
    User, Group, GroupMembership, Expense, ExpenseShare, Payment,
    ImportBatch, StagedExpense, StagedExpenseShare, StagedAnomaly,
    ImportDecision, ExchangeRate, BalanceSnapshot, AuditLog
)
from app.database import get_db

# Name normalization mapping
CANONICAL_NAMES = {
    "aisha": "Aisha", "aisha m.": "Aisha", "aisha m": "Aisha", "aisha.m": "Aisha",
    "rohan": "Rohan", "rohan s.": "Rohan", "rohan s": "Rohan", "rohan.s": "Rohan",
    "priya": "Priya", "priya k.": "Priya", "priya k": "Priya", "priya.k": "Priya",
    "sam": "Sam", "sam l.": "Sam", "sam l": "Sam", "sam.l": "Sam",
    "meera": "Meera", "meera p.": "Meera", "meera p": "Meera", "meera.p": "Meera"
}

def normalize_name(name_str: str) -> str:
    """
    Standardizes user names by removing extra spaces, casing, and mapping variations.
    """
    if not name_str:
        return ""
    cleaned = name_str.strip().lower()
    return CANONICAL_NAMES.get(cleaned, name_str.strip().title())

def parse_amount(amount_str: str) -> tuple[int, str]:
    """
    Parses amount string containing currency symbols and returns amount in minor units (paise)
    along with the detected currency symbol.
    """
    if not amount_str:
        return 0, "INR"
    
    cleaned = amount_str.strip()
    currency = "INR"
    
    if "$" in cleaned or "USD" in cleaned.upper():
        currency = "USD"
    elif "€" in cleaned or "EUR" in cleaned.upper():
        currency = "EUR"
    
    # Strip non-numeric/non-decimal characters, preserving minus sign
    numeric_chars = []
    for c in cleaned:
        if c.isdigit() or c == "." or c == "-":
            numeric_chars.append(c)
    
    parsed_str = "".join(numeric_chars)
    if not parsed_str or parsed_str == "-":
        return 0, currency
        
    try:
        val = float(parsed_str)
        # Convert to paise (cents) as an integer
        return int(round(val * 100)), currency
    except ValueError:
        return 0, currency

def parse_date(date_str: str) -> datetime.date | None:
    """
    Attempts to parse date string with fallback support for multiple formats.
    """
    if not date_str:
        return None
        
    formats = [
        "%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%d/%m/%y", "%m/%d/%y",
        "%B %d, %Y", "%b %d, %Y", "%d %B %Y", "%d %b %Y"
    ]
    
    cleaned = date_str.strip()
    for fmt in formats:
        try:
            return datetime.datetime.strptime(cleaned, fmt).date()
        except ValueError:
            continue
            
    return None

def detect_anomalies_for_row(
    row_dict: dict, 
    row_num: int, 
    group_id: int, 
    db: Session,
    batch_id: int
) -> tuple[StagedExpense, list[dict]]:
    """
    Analyzes a CSV row for 16 target anomalies, creates a StagedExpense,
    and returns a list of anomaly definitions to write.
    """
    raw_date = row_dict.get("Date", "")
    raw_desc = row_dict.get("Description", "")
    raw_amount_str = row_dict.get("Amount", "")
    raw_currency_val = row_dict.get("Currency", "")
    raw_paid_by = row_dict.get("Paid By", "")
    raw_split_type = row_dict.get("Split Type", "EQUAL")
    raw_split_details = row_dict.get("Split Details", "")
    category = row_dict.get("Category", "General")

    anomalies = []

    # 1. Parse Date (A12 - Malformed Date, A15 - Future Date)
    parsed_date = parse_date(raw_date)
    is_future = False
    if not parsed_date:
        anomalies.append({
            "anomaly_type": "MALFORMED_DATE",
            "severity": "ERROR",
            "description": f"Date '{raw_date}' is malformed and cannot be parsed.",
            "proposed_correction": "Provide a date in YYYY-MM-DD format."
        })
    else:
        if parsed_date > datetime.date.today():
            is_future = True
            anomalies.append({
                "anomaly_type": "FUTURE_DATE",
                "severity": "WARNING",
                "description": f"Date '{parsed_date}' is in the future.",
                "proposed_correction": "Verify if the date is correct, or keep it."
            })

    # 2. Parse Amount (A02 - Missing Currency, A03 - Foreign Currency, A04 - Currency Symbol, A11 - Negative/Zero, A16 - Outlier)
    amount_paise, currency = parse_amount(raw_amount_str)
    
    # Override currency if raw currency field is set explicitly
    if raw_currency_val:
        raw_curr_cleaned = raw_currency_val.strip().upper()
        if raw_curr_cleaned in ["USD", "EUR", "INR"]:
            currency = raw_curr_cleaned

    # A02 - Missing Currency Check
    if not raw_currency_val and "$" not in raw_amount_str and "₹" not in raw_amount_str and "USD" not in raw_amount_str.upper() and "EUR" not in raw_amount_str.upper():
        anomalies.append({
            "anomaly_type": "MISSING_CURRENCY",
            "severity": "INFO",
            "description": "No currency specified. Defaulting to INR.",
            "proposed_correction": "Assume INR."
        })

    # A04 - Currency Symbol in Amount Check
    if any(sym in raw_amount_str for sym in ["₹", "$", "€", "Rs"]):
        anomalies.append({
            "anomaly_type": "CURRENCY_SYMBOL_IN_AMOUNT",
            "severity": "INFO",
            "description": f"Amount '{raw_amount_str}' contains currency symbols/formatting.",
            "proposed_correction": f"Clean amount to numeric value: {amount_paise / 100:.2f}."
        })

    # A03 - Foreign Currency Conversion Check
    exchange_rate = Decimal("1.000000")
    converted_amount_paise = amount_paise
    if currency != "INR":
        rate_record = db.query(ExchangeRate).filter(ExchangeRate.currency == currency).first()
        if rate_record:
            exchange_rate = rate_record.rate_to_inr
        else:
            # Fallback exchange rates if not pre-seeded
            fallback_rates = {"USD": Decimal("83.000000"), "EUR": Decimal("90.000000")}
            exchange_rate = fallback_rates.get(currency, Decimal("1.000000"))
        
        converted_amount_paise = int(round(float(amount_paise) * float(exchange_rate)))
        anomalies.append({
            "anomaly_type": "FOREIGN_CURRENCY",
            "severity": "INFO",
            "description": f"Currency is {currency}. Converted amount is ₹{converted_amount_paise / 100:.2f} at rate {exchange_rate:.2f}.",
            "proposed_correction": f"Convert to base INR at exchange rate {exchange_rate}."
        })

    # A11 - Negative/Zero Amount Check (Refund Candidates)
    if amount_paise < 0:
        anomalies.append({
            "anomaly_type": "NEGATIVE_AMOUNT",
            "severity": "WARNING",
            "description": f"Amount is negative ({amount_paise / 100:.2f}). Evaluated as Refund/Reimbursement Candidate.",
            "proposed_correction": "Treat as group refund/reimbursement."
        })
    elif amount_paise == 0:
        anomalies.append({
            "anomaly_type": "ZERO_AMOUNT",
            "severity": "ERROR",
            "description": "Amount is zero. Transactions cannot be empty.",
            "proposed_correction": "Correct the transaction amount or reject the row."
        })

    # A16 - Outlier Amount Check
    if converted_amount_paise > 10000000: # ₹100,000 in paise
        anomalies.append({
            "anomaly_type": "OUTLIER_AMOUNT",
            "severity": "WARNING",
            "description": f"Large expense amount detected: ₹{converted_amount_paise / 100:.2f}.",
            "proposed_correction": "Verify if amount is correct (not a typo)."
        })

    # 3. Payer Name (A05 - Inconsistent Name, A06 - Payer Not Registered, A07 - Payer Outside Tenure)
    norm_payer = normalize_name(raw_paid_by)
    payer_user = db.query(User).filter(User.username == norm_payer).first()

    # A05 - Inconsistent Casing/Spaces Check
    if raw_paid_by and (raw_paid_by != norm_payer or raw_paid_by.lower() != norm_payer.lower()):
        anomalies.append({
            "anomaly_type": "INCONSISTENT_NAME_CASING",
            "severity": "INFO",
            "description": f"Payer name '{raw_paid_by}' was normalized to canonical '{norm_payer}'.",
            "proposed_correction": f"Map payer to user ID of '{norm_payer}'."
        })

    if not payer_user:
        anomalies.append({
            "anomaly_type": "PAYER_NOT_REGISTERED",
            "severity": "ERROR",
            "description": f"Payer '{raw_paid_by}' does not map to any registered user in database.",
            "proposed_correction": "Add user or map to existing user."
        })
    elif parsed_date:
        # Check if active member on expense date
        membership = db.query(GroupMembership).filter(
            GroupMembership.group_id == group_id,
            GroupMembership.user_id == payer_user.id,
            GroupMembership.joined_date <= parsed_date,
            (GroupMembership.left_date == None) | (GroupMembership.left_date >= parsed_date)
        ).first()
        
        if not membership:
            anomalies.append({
                "anomaly_type": "PAYER_OUTSIDE_TENURE",
                "severity": "ERROR",
                "description": f"Payer '{norm_payer}' was not an active member on {parsed_date}.",
                "proposed_correction": "Review group membership joined/left dates or adjust expense date."
            })

    # 4. Empty Description (A14)
    desc = raw_desc.strip() if raw_desc else ""
    if not desc:
        desc = f"Imported Expense {category}"
        anomalies.append({
            "anomaly_type": "EMPTY_DESCRIPTION",
            "severity": "INFO",
            "description": "Description is empty.",
            "proposed_correction": f"Set default description to '{desc}'."
        })

    # 5. Settlement Check (A13)
    split_type = raw_split_type.strip().upper() if raw_split_type else "EQUAL"
    if not raw_split_details and (not raw_split_type or split_type == "EQUAL") and any(term in desc.lower() for term in ["settle", "paid back"]):
        anomalies.append({
            "anomaly_type": "SETTLEMENT_IN_EXPENSE_SHEET",
            "severity": "WARNING",
            "description": f"Expense description '{desc}' suggests this is a peer-to-peer settlement.",
            "proposed_correction": "Import as Payment settlement instead of group Expense."
        })

    # 6. Duplicates Check (A01)
    if parsed_date and payer_user:
        # Check production DB duplicates
        dupe_check = db.query(Expense).filter(
            Expense.group_id == group_id,
            Expense.date == parsed_date,
            Expense.converted_amount_paise == converted_amount_paise,
            Expense.paid_by_id == payer_user.id,
            Expense.description == desc
        ).first()
        
        # Check staged duplicates in this batch
        dupe_staged_check = db.query(StagedExpense).filter(
            StagedExpense.batch_id == batch_id,
            StagedExpense.raw_date == raw_date,
            StagedExpense.raw_amount == raw_amount_str,
            StagedExpense.raw_paid_by == raw_paid_by,
            StagedExpense.description == desc
        ).first()

        if dupe_check or dupe_staged_check:
            anomalies.append({
                "anomaly_type": "DUPLICATE_ROW",
                "severity": "WARNING",
                "description": f"Duplicate transaction matching '{desc}' of ₹{converted_amount_paise/100:.2f} on {parsed_date} found.",
                "proposed_correction": "Skip this row to avoid double counting."
            })

    # 7. Split Details Validation (A08 - Split Outside Tenure, A09 - Percent total, A10 - Exact total)
    staged_shares = []
    
    if parsed_date:
        # Fetch active members for dynamic split checking
        active_memberships = db.query(GroupMembership).filter(
            GroupMembership.group_id == group_id,
            GroupMembership.joined_date <= parsed_date,
            (GroupMembership.left_date == None) | (GroupMembership.left_date >= parsed_date)
        ).all()
        active_user_ids = {m.user_id for m in active_memberships}
        active_users_map = {db.query(User).filter(User.id == m.user_id).first().username: m.user_id for m in active_memberships}
    else:
        active_user_ids = set()
        active_users_map = {}

    if parsed_date and split_type == "EQUAL" and not raw_split_details:
        # Dynamic EQUAL split calculation based on tenure
        if active_user_ids:
            # Convert to list for deterministic ordering and payer-preferred remainder allocation
            active_uids_list = list(active_user_ids)
            share_paise = converted_amount_paise // len(active_uids_list)
            remainder = converted_amount_paise % len(active_uids_list)

            # Determine which index receives the remainder — prefer the payer
            payer_idx = 0  # fallback to first member
            if payer_user and payer_user.id in active_user_ids:
                payer_idx = active_uids_list.index(payer_user.id)

            for idx, uid in enumerate(active_uids_list):
                amt = share_paise
                if idx == payer_idx:
                    amt += remainder
                staged_shares.append({"user_id": uid, "share_amount_paise": amt})
        else:
            anomalies.append({
                "anomaly_type": "NO_ACTIVE_MEMBERS",
                "severity": "ERROR",
                "description": "No active group members on this date to split with.",
                "proposed_correction": "Modify membership dates or expense date."
            })

    elif parsed_date and raw_split_details:
        # Parse details e.g., "Aisha:30;Rohan:30;Priya:40"
        parts = [p.strip() for p in raw_split_details.split(";") if p.strip()]
        split_entries = []
        
        for p in parts:
            if ":" in p:
                name_part, val_part = p.split(":", 1)
                split_entries.append((normalize_name(name_part), val_part.strip()))
            else:
                split_entries.append((normalize_name(p), None))
                
        # Validate members are registered and active (A08)
        valid_splits = []
        for name, val in split_entries:
            member = db.query(User).filter(User.username == name).first()
            if not member:
                anomalies.append({
                    "anomaly_type": "SPLIT_MEMBER_NOT_REGISTERED",
                    "severity": "ERROR",
                    "description": f"Split member '{name}' is not registered.",
                    "proposed_correction": "Add user or map to existing user."
                })
            elif member.id not in active_user_ids:
                anomalies.append({
                    "anomaly_type": "SPLIT_MEMBER_OUTSIDE_TENURE",
                    "severity": "WARNING",
                    "description": f"Split member '{name}' was not active on {parsed_date}.",
                    "proposed_correction": f"Exclude '{name}' and redistribute share to active members."
                })
            else:
                valid_splits.append((member.id, name, val))

        # Perform split math based on type
        if valid_splits:
            if split_type == "PERCENT":
                total_pct = sum(float(v.replace("%", "")) for _, _, v in valid_splits if v)
                if abs(total_pct - 100.0) > 0.001:
                    anomalies.append({
                        "anomaly_type": "PERCENTAGE_SUM_ERROR",
                        "severity": "WARNING",
                        "description": f"Percentages sum to {total_pct}%, which is not 100%.",
                        "proposed_correction": "Normalize percentages to sum to exactly 100%."
                    })
                    
                # Normalize and calculate
                factor = 100.0 / total_pct if total_pct > 0 else 1.0
                running_total = 0
                for idx, (uid, name, val_str) in enumerate(valid_splits):
                    pct = float(val_str.replace("%", "")) if val_str else 0.0
                    normalized_pct = pct * factor
                    share_paise = int(round(converted_amount_paise * (normalized_pct / 100.0)))
                    
                    if idx == len(valid_splits) - 1:
                        share_paise = converted_amount_paise - running_total
                    running_total += share_paise
                    staged_shares.append({"user_id": uid, "share_amount_paise": share_paise})
                    
            elif split_type == "EXACT":
                raw_shares = []
                for uid, name, val_str in valid_splits:
                    # Clean and parse share amount
                    sh_paise, _ = parse_amount(val_str)
                    raw_shares.append((uid, sh_paise))
                    
                total_exact = sum(v for _, v in raw_shares)
                if total_exact != converted_amount_paise:
                    anomalies.append({
                        "anomaly_type": "EXACT_SUM_ERROR",
                        "severity": "WARNING",
                        "description": f"Exact shares sum to {total_exact/100:.2f}, while expense is {converted_amount_paise/100:.2f}.",
                        "proposed_correction": "Adjust the rounding difference to the payer's share."
                    })
                    
                diff = converted_amount_paise - total_exact
                running_total = 0
                for idx, (uid, sh_paise) in enumerate(raw_shares):
                    amt = sh_paise
                    if uid == (payer_user.id if payer_user else None):
                        amt += diff
                    staged_shares.append({"user_id": uid, "share_amount_paise": amt})
            else: # EQUAL with details
                share_paise = converted_amount_paise // len(valid_splits)
                remainder = converted_amount_paise % len(valid_splits)
                for idx, (uid, name, _) in enumerate(valid_splits):
                    amt = share_paise
                    if idx == 0:
                        amt += remainder
                    staged_shares.append({"user_id": uid, "share_amount_paise": amt})

    # Construct the StagedExpense model record
    # Deduce final row status
    has_errors = any(a["severity"] == "ERROR" for a in anomalies)
    has_warnings = any(a["severity"] == "WARNING" for a in anomalies)
    
    staged_status = "PENDING_APPROVAL"
    if not anomalies:
        staged_status = "APPROVED" # Clean row

    staged_exp = StagedExpense(
        batch_id=batch_id,
        row_number=row_num,
        description=desc,
        raw_amount=raw_amount_str,
        raw_currency=currency,
        raw_date=raw_date,
        raw_paid_by=raw_paid_by,
        raw_split_type=raw_split_type,
        raw_split_details=raw_split_details,
        category=category,
        status=staged_status
    )

    return staged_exp, anomalies, staged_shares

def parse_csv_to_staging(file_path: str, batch_id: int, group_id: int, db: Session):
    """
    Reads the raw CSV file, triggers anomaly analysis, and writes staged objects to database.
    """
    with open(file_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for idx, row in enumerate(reader, start=1):
            staged_exp, anomalies, shares = detect_anomalies_for_row(row, idx, group_id, db, batch_id)
            
            db.add(staged_exp)
            db.flush() # Yields staged_exp.id
            
            # Write shares
            for sh in shares:
                db.add(StagedExpenseShare(
                    staged_expense_id=staged_exp.id,
                    user_id=sh["user_id"],
                    share_amount_paise=sh["share_amount_paise"]
                ))
                
            # Write anomalies
            for an in anomalies:
                db.add(StagedAnomaly(
                    staged_expense_id=staged_exp.id,
                    anomaly_type=an["anomaly_type"],
                    severity=an["severity"],
                    description=an["description"],
                    proposed_correction=an["proposed_correction"]
                ))
            
            db.commit()

def process_decision(
    staged_id: int, 
    decision_type: str, 
    user_id: int, 
    modifications: dict, 
    db: Session
) -> tuple[str, int | None]:
    """
    Applies user decision to a staged row. Promotes to production database
    if approved (or modified-approved), logs history.
    """
    staged_exp = db.query(StagedExpense).filter(StagedExpense.id == staged_id).first()
    if not staged_exp:
        return "NOT_FOUND", None

    if decision_type == "SKIPPED":
        staged_exp.status = "REJECTED"
        # Record decisions for each anomaly
        for an in staged_exp.anomalies:
            db.add(ImportDecision(
                anomaly_id=an.id,
                decision="SKIPPED",
                decision_by=user_id,
                notes="Skipped by user review."
            ))
        db.commit()
        return "REJECTED", None

    # Determine database attributes, merging overrides
    desc = modifications.get("description", staged_exp.description)
    category = modifications.get("category", staged_exp.category)
    
    # Process modifications on date
    date_val = modifications.get("date")
    if isinstance(date_val, str):
        parsed_date = parse_date(date_val)
    elif isinstance(date_val, datetime.date):
        parsed_date = date_val
    else:
        parsed_date = parse_date(staged_exp.raw_date)

    # Process modifications on raw amount
    raw_amount = modifications.get("raw_amount", staged_exp.raw_amount)
    amount_paise, currency = parse_amount(raw_amount)
    
    if currency != "INR":
        rate_record = db.query(ExchangeRate).filter(ExchangeRate.currency == currency).first()
        exchange_rate = rate_record.rate_to_inr if rate_record else Decimal("83.000000")
        converted_amount_paise = int(round(float(amount_paise) * float(exchange_rate)))
    else:
        exchange_rate = Decimal("1.000000")
        converted_amount_paise = amount_paise

    payer_id = modifications.get("paid_by_id")
    if not payer_id:
        norm_payer = normalize_name(staged_exp.raw_paid_by)
        payer_user = db.query(User).filter(User.username == norm_payer).first()
        payer_id = payer_user.id if payer_user else None

    split_type = modifications.get("split_type", staged_exp.raw_split_type)
    
    # Gather group context
    batch = db.query(ImportBatch).filter(ImportBatch.id == staged_exp.batch_id).first()
    if not batch:
        return "BATCH_NOT_FOUND", None

    # Resolve group_id from batch (authoritative) or fall back to user's first membership
    group_id = batch.group_id
    if not group_id:
        group_memb = db.query(GroupMembership).filter(GroupMembership.user_id == user_id).first()
        if not group_memb:
            return "NO_GROUP", None
        group_id = group_memb.group_id

    # Check if this row is evaluated as a Settlement (Payment)
    is_settlement_payment = False
    if split_type == "EQUAL" and any(term in desc.lower() for term in ["settle", "paid back"]):
        is_settlement_payment = True

    promoted_id = None

    if is_settlement_payment:
        # Map target from splits details
        target_id = None
        staged_shares = staged_exp.shares
        if staged_shares:
            # Payment destination is the first shareholder who is not the payer
            for sh in staged_shares:
                if sh.user_id != payer_id:
                    target_id = sh.user_id
                    break
        
        if not target_id:
            # Fallback to group membership
            fallback_target = db.query(GroupMembership).filter(
                GroupMembership.group_id == group_id,
                GroupMembership.user_id != payer_id
            ).first()
            target_id = fallback_target.user_id if fallback_target else payer_id

        # Insert to payments
        payment = Payment(
            group_id=group_id,
            from_user_id=payer_id,
            to_user_id=target_id,
            amount_paise=abs(converted_amount_paise), # Ensure absolute payment
            date=parsed_date or datetime.date.today(),
            notes=f"Converted from Staged Row #{staged_exp.row_number}: {desc}",
            import_batch_id=batch.id
        )
        db.add(payment)
        db.flush()
        promoted_id = payment.id
        
        # Log audit trail
        db.add(AuditLog(
            user_id=user_id,
            action="PROMOTE_AS_PAYMENT",
            target_type="payment",
            target_id=payment.id,
            details={"raw_row": staged_exp.row_number, "amount": payment.amount_paise}
        ))
    else:
        # Insert to expenses
        expense = Expense(
            group_id=group_id,
            description=desc,
            original_amount_paise=amount_paise,
            original_currency=currency,
            exchange_rate=exchange_rate,
            converted_amount_paise=converted_amount_paise,
            date=parsed_date or datetime.date.today(),
            paid_by_id=payer_id,
            split_type=split_type,
            category=category,
            import_batch_id=batch.id
        )
        db.add(expense)
        db.flush()
        promoted_id = expense.id

        # Insert shares
        shares_modified = modifications.get("shares") # list of dict: [{"user_id": 1, "share_amount_paise": 100}]
        if shares_modified:
            for sh in shares_modified:
                db.add(ExpenseShare(
                    expense_id=expense.id,
                    user_id=sh["user_id"],
                    share_amount_paise=sh["share_amount_paise"]
                ))
        else:
            # Copy from staging shares
            for sh in staged_exp.shares:
                db.add(ExpenseShare(
                    expense_id=expense.id,
                    user_id=sh.user_id,
                    share_amount_paise=sh.share_amount_paise
                ))
                
        # Log audit trail
        db.add(AuditLog(
            user_id=user_id,
            action="PROMOTE_AS_EXPENSE",
            target_type="expense",
            target_id=expense.id,
            details={"raw_row": staged_exp.row_number, "amount": expense.converted_amount_paise}
        ))

    # Record decisions for each anomaly
    for an in staged_exp.anomalies:
        db.add(ImportDecision(
            anomaly_id=an.id,
            decision="APPROVED_AS_IS" if decision_type == "APPROVED" else "MODIFIED",
            decision_by=user_id,
            notes=modifications.get("notes", "Resolved by reviewer.")
        ))

    staged_exp.status = "APPROVED" if decision_type == "APPROVED" else "MODIFIED"
    db.commit()
    
    return staged_exp.status, promoted_id
