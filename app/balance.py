import datetime
from decimal import Decimal
from sqlalchemy.orm import Session
from sqlalchemy import func
from app.models import (
    User, Group, GroupMembership, Expense, ExpenseShare, Payment,
    BalanceSnapshot, AuditLog
)

def recalculate_snapshots(group_id: int, db: Session):
    """
    Performs full ledger aggregation and updates the balance_snapshots table
    for all group members. Executed after ledger mutations.
    """
    # Fetch all members of the group
    memberships = db.query(GroupMembership).filter(GroupMembership.group_id == group_id).all()
    user_ids = {m.user_id for m in memberships}

    for uid in user_ids:
        # 1. Total paid by user in group
        paid_amt = db.query(func.sum(Expense.converted_amount_paise)).filter(
            Expense.group_id == group_id,
            Expense.paid_by_id == uid
        ).scalar() or 0

        # 2. Total owed by user in group
        owed_amt = db.query(func.sum(ExpenseShare.share_amount_paise)).join(Expense).filter(
            Expense.group_id == group_id,
            ExpenseShare.user_id == uid
        ).scalar() or 0

        # 3. Total settlements sent by user
        sent_amt = db.query(func.sum(Payment.amount_paise)).filter(
            Payment.group_id == group_id,
            Payment.from_user_id == uid
        ).scalar() or 0

        # 4. Total settlements received by user
        recv_amt = db.query(func.sum(Payment.amount_paise)).filter(
            Payment.group_id == group_id,
            Payment.to_user_id == uid
        ).scalar() or 0

        # Net balance formula derivation:
        #
        #   paid_amt  = cash user fronted for group expenses.
        #               The group owes the user this money back. (+)
        #
        #   owed_amt  = user's share of all group expenses.
        #               The user owes this for their own consumption. (-)
        #
        #   sent_amt  = cash settlements user SENT to others.
        #               Sending a settlement pays off the user's own debt,
        #               which reduces what they owe — improving their net. (+)
        #
        #   recv_amt  = cash settlements user RECEIVED from others.
        #               Receiving a settlement collects what is owed to the user,
        #               which reduces the remaining claim — lowering their net. (-)
        #
        #   net = (paid_amt + sent_amt) - (owed_amt + recv_amt)
        #       = paid_amt - owed_amt + sent_amt - recv_amt
        #
        #   Invariant: sum of all members' net_balance_paise for a group == 0
        net_balance = (paid_amt + sent_amt) - (owed_amt + recv_amt)

        # Update or create snapshot
        snap = db.query(BalanceSnapshot).filter(
            BalanceSnapshot.group_id == group_id,
            BalanceSnapshot.user_id == uid
        ).first()

        if not snap:
            snap = BalanceSnapshot(
                group_id=group_id,
                user_id=uid,
                net_balance_paise=net_balance,
                paid_amt_paise=paid_amt,
                owed_amt_paise=owed_amt,
                settlements_sent_paise=sent_amt,
                settlements_received_paise=recv_amt
            )
            db.add(snap)
        else:
            snap.net_balance_paise = net_balance
            snap.paid_amt_paise = paid_amt
            snap.owed_amt_paise = owed_amt
            snap.settlements_sent_paise = sent_amt
            snap.settlements_received_paise = recv_amt
            snap.calculated_at = datetime.datetime.utcnow()
            
    db.commit()

def explain_balance_trace(user_id: int, group_id: int, db: Session) -> list[dict]:
    """
    Builds a chronological list of ledger events affecting a user's net balance.
    This guarantees full auditability for Rohan's traceability requirement.
    """
    events = []

    # 1. Gather expenses paid by user
    paid_expenses = db.query(Expense).filter(
        Expense.group_id == group_id,
        Expense.paid_by_id == user_id
    ).all()
    for exp in paid_expenses:
        events.append({
            "date": exp.date,
            "type": "PAYMENT_OUT", # User paid cash up front
            "description": f"Paid for: {exp.description}",
            "amount_paise": exp.converted_amount_paise,
            "original_amount": exp.original_amount_paise / 100.0,
            "original_currency": exp.original_currency,
            "exchange_rate": exp.exchange_rate,
            "effect_paise": exp.converted_amount_paise,
            "role": "PAYER"
        })

    # 2. Gather shares owed by user
    owed_shares = db.query(ExpenseShare).join(Expense).filter(
        Expense.group_id == group_id,
        ExpenseShare.user_id == user_id
    ).all()
    for sh in owed_shares:
        exp = sh.expense
        events.append({
            "date": exp.date,
            "type": "DEBIT_SHARE", # User owes money for their split
            "description": f"Split share for: {exp.description} (Paid by {exp.paid_by.username})",
            "amount_paise": sh.share_amount_paise,
            "original_amount": (sh.share_amount_paise / float(exp.exchange_rate)) / 100.0 if float(exp.exchange_rate) > 0 else sh.share_amount_paise / 100.0,
            "original_currency": exp.original_currency,
            "exchange_rate": exp.exchange_rate,
            "effect_paise": -sh.share_amount_paise,
            "role": "SHAREHOLDER"
        })

    # 3. Gather payments sent by user
    sent_payments = db.query(Payment).filter(
        Payment.group_id == group_id,
        Payment.from_user_id == user_id
    ).all()
    for pm in sent_payments:
        events.append({
            "date": pm.date,
            "type": "SETTLEMENT_SENT",
            "description": f"Settlement sent to {pm.to_user.username}",
            "amount_paise": pm.amount_paise,
            "original_amount": pm.amount_paise / 100.0,
            "original_currency": "INR",
            "exchange_rate": Decimal("1.0"),
            "effect_paise": pm.amount_paise,
            "role": "SENDER"
        })

    # 4. Gather payments received by user
    received_payments = db.query(Payment).filter(
        Payment.group_id == group_id,
        Payment.to_user_id == user_id
    ).all()
    for pm in received_payments:
        events.append({
            "date": pm.date,
            "type": "SETTLEMENT_RECV",
            "description": f"Settlement received from {pm.from_user.username}",
            "amount_paise": pm.amount_paise,
            "original_amount": pm.amount_paise / 100.0,
            "original_currency": "INR",
            "exchange_rate": Decimal("1.0"),
            "effect_paise": -pm.amount_paise,
            "role": "RECEIVER"
        })

    # Sort events chronologically by date, secondary key on description to keep stable
    events.sort(key=lambda x: (x["date"], x["description"]))

    # Add running total trace
    running_balance = 0
    for ev in events:
        running_balance += ev["effect_paise"]
        ev["running_balance_paise"] = running_balance

    return events

def simplify_debts(group_id: int, db: Session) -> list[dict]:
    """
    Computes the minimal transactions required to clear group debts.
    Greedy min-max balance-matching algorithm.
    """
    snapshots = db.query(BalanceSnapshot).filter(BalanceSnapshot.group_id == group_id).all()
    
    # Extract net balances and verify they balance out to zero
    net_balances = {}
    for snap in snapshots:
        net_balances[snap.user_id] = snap.net_balance_paise

    total_net = sum(net_balances.values())
    # Correct for minor rounding errors if any exist
    if abs(total_net) > 0 and len(net_balances) > 0:
        # Distribute the discrepancy to the first active user
        first_uid = list(net_balances.keys())[0]
        net_balances[first_uid] -= total_net

    # Construct active lists
    debtors = [] # values are negative (who owes money)
    creditors = [] # values are positive (who is owed money)

    # Resolve users
    for uid, bal in net_balances.items():
        user = db.query(User).filter(User.id == uid).first()
        username = user.username if user else f"User {uid}"
        
        if bal < -1: # Tolerate < 1 paise error
            debtors.append({"id": uid, "username": username, "balance": bal})
        elif bal > 1:
            creditors.append({"id": uid, "username": username, "balance": bal})

    instructions = []

    # Greedy match
    while debtors and creditors:
        # Sort so largest balances are resolved first
        debtors.sort(key=lambda x: x["balance"]) # Most negative first
        creditors.sort(key=lambda x: x["balance"], reverse=True) # Most positive first

        d = debtors[0]
        c = creditors[0]

        amt_to_settle = min(abs(d["balance"]), c["balance"])
        if amt_to_settle <= 0:
            break

        instructions.append({
            "from_user_id": d["id"],
            "from_username": d["username"],
            "to_user_id": c["id"],
            "to_username": c["username"],
            "amount_paise": amt_to_settle
        })

        # Update remaining balances
        d["balance"] += amt_to_settle
        c["balance"] -= amt_to_settle

        # Filter out nodes that reached zero balance
        debtors = [x for x in debtors if x["balance"] < -1]
        creditors = [x for x in creditors if x["balance"] > 1]

    return instructions
