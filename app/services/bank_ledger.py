from calendar import monthrange
from datetime import date, timedelta

from sqlalchemy.orm import selectinload

from app import db
from app.models import BankAccount, BankLedgerEntry, BankTransfer


def _format_money(value: float) -> str:
    return f"{value:,.2f}"


def normalize_expense_category(value: str | None) -> str:
    cleaned = (value or "").strip().lower()
    allowed = {
        BankLedgerEntry.CATEGORY_GENERAL,
        BankLedgerEntry.CATEGORY_BILTY,
        BankLedgerEntry.CATEGORY_EXTRA_EXPENSES,
        BankLedgerEntry.CATEGORY_LOADING_KHARCHA,
    }
    return cleaned if cleaned in allowed else BankLedgerEntry.CATEGORY_GENERAL


def _is_standard_entry(entry: BankLedgerEntry) -> bool:
    """Daily entries only — excludes cross-bank transfer ledger rows."""
    return entry.entry_type == BankLedgerEntry.TYPE_STANDARD


def _serialize_rokar_entry(entry: BankLedgerEntry) -> dict:
    deposit = float(entry.deposit or 0)
    withdrawal = float(entry.withdrawal or 0)
    return {
        "entry": entry,
        "bank": entry.bank,
        "particulars": format_entry_particulars(entry),
        "deposit": deposit,
        "withdrawal": withdrawal,
        "type_label": entry.type_label,
        "category_label": entry.expense_category_label,
        "expense_category": entry.expense_category or "",
    }


def _category_sections(transactions: list[dict]) -> dict:
    sections = {
        BankLedgerEntry.CATEGORY_BILTY: {
            "label": "Bilty",
            "transactions": [],
            "total_deposits": 0.0,
            "total_withdrawals": 0.0,
        },
        BankLedgerEntry.CATEGORY_EXTRA_EXPENSES: {
            "label": "Extra Expenses",
            "transactions": [],
            "total_deposits": 0.0,
            "total_withdrawals": 0.0,
        },
        BankLedgerEntry.CATEGORY_LOADING_KHARCHA: {
            "label": "Loading Kharcha",
            "transactions": [],
            "total_deposits": 0.0,
            "total_withdrawals": 0.0,
        },
    }
    for row in transactions:
        category = row.get("expense_category") or ""
        if category not in sections:
            continue
        sections[category]["transactions"].append(row)
        sections[category]["total_deposits"] += row["deposit"]
        sections[category]["total_withdrawals"] += row["withdrawal"]
    return sections


def format_entry_particulars(entry: BankLedgerEntry) -> str:
    if entry.entry_type == BankLedgerEntry.TYPE_TRANSFER_OUT and entry.counterparty_bank:
        parts = [f"Transfer to {entry.counterparty_bank.display_name}"]
    elif entry.entry_type == BankLedgerEntry.TYPE_TRANSFER_IN and entry.counterparty_bank:
        parts = [f"Transfer from {entry.counterparty_bank.display_name}"]
    else:
        parts = []
    if entry.notes:
        parts.append(entry.notes)
    if entry.transfer and entry.transfer.reference:
        parts.append(f"Ref: {entry.transfer.reference}")
    return " · ".join(parts) if parts else "—"


def get_bank_balance_as_of(bank: BankAccount, as_of_date: date) -> float:
    balance = float(bank.opening_balance or 0)
    entries = (
        bank.entries.filter(BankLedgerEntry.entry_date <= as_of_date)
        .order_by(BankLedgerEntry.entry_date.asc(), BankLedgerEntry.id.asc())
        .all()
    )
    for entry in entries:
        balance += float(entry.deposit or 0) - float(entry.withdrawal or 0)
    return balance


def get_bank_balances(banks, as_of_date: date | None = None, opening_date: date | None = None) -> dict:
    """Balances of many banks from ONE query.

    Returns {bank_id: [balance, entry_count, opening_balance]} where balance is exactly
    get_bank_balance(bank) (or get_bank_balance_as_of(bank, as_of_date)) and opening_balance is
    get_bank_balance_as_of(bank, opening_date): same entries, same (entry_date, id) order, same
    float arithmetic as the per-bank versions — only the rows of all banks come in together.
    """
    state = {}
    for bank in banks:
        start = float(bank.opening_balance or 0)
        state[bank.id] = [start, 0, start]
    if not state:
        return state
    columns = [BankLedgerEntry.bank_id, BankLedgerEntry.deposit, BankLedgerEntry.withdrawal]
    if opening_date is not None:
        # The database decides "entry_date <= opening_date", exactly as the per-bank query did.
        columns.append((BankLedgerEntry.entry_date <= opening_date).label("in_opening"))
    query = db.session.query(*columns)
    if as_of_date is not None:
        query = query.filter(BankLedgerEntry.entry_date <= as_of_date)
    query = query.order_by(BankLedgerEntry.entry_date.asc(), BankLedgerEntry.id.asc())
    for row in query:
        current = state.get(row[0])
        if current is None:
            continue
        change = float(row[1] or 0) - float(row[2] or 0)
        current[0] += change
        current[1] += 1
        if opening_date is not None and row[3]:
            current[2] += change
    return state


def _rokar_banks(banks):
    if banks is None:
        banks = BankAccount.query.order_by(BankAccount.bank_name, BankAccount.account_number).all()
    return banks


def get_rokar_day_data(entry_date: date, banks=None) -> dict:
    # Banks first so entry.bank and transfer counterparties are found without extra queries.
    banks = _rokar_banks(banks)
    entries = (
        BankLedgerEntry.query.join(BankAccount)
        .options(selectinload(BankLedgerEntry.transfer))
        .filter(BankLedgerEntry.entry_date == entry_date)
        .order_by(BankLedgerEntry.id.asc())
        .all()
    )
    return _build_rokar_report(
        entries=entries,
        period_label=entry_date.strftime("%d-%b-%Y"),
        start_date=entry_date,
        end_date=entry_date,
        banks=banks,
    )


def get_rokar_month_data(year: int, month: int, banks=None) -> dict:
    start_date = date(year, month, 1)
    end_date = date(year, month, monthrange(year, month)[1])
    banks = _rokar_banks(banks)
    entries = (
        BankLedgerEntry.query.join(BankAccount)
        .options(selectinload(BankLedgerEntry.transfer))
        .filter(
            BankLedgerEntry.entry_date >= start_date,
            BankLedgerEntry.entry_date <= end_date,
        )
        .order_by(BankLedgerEntry.entry_date.asc(), BankLedgerEntry.id.asc())
        .all()
    )
    return _build_rokar_report(
        entries=entries,
        period_label=start_date.strftime("%B %Y"),
        start_date=start_date,
        end_date=end_date,
        banks=banks,
    )


def _build_rokar_report(
    entries: list[BankLedgerEntry],
    period_label: str,
    start_date: date,
    end_date: date,
    banks=None,
) -> dict:
    banks = _rokar_banks(banks)

    transactions = []
    total_deposits = 0.0
    total_withdrawals = 0.0
    for entry in entries:
        row = _serialize_rokar_entry(entry)
        if _is_standard_entry(entry):
            total_deposits += row["deposit"]
            total_withdrawals += row["withdrawal"]
        transactions.append(row)

    bank_balances = []
    total_closing = 0.0
    total_opening = 0.0
    opening_reference = start_date - timedelta(days=1)
    balances = get_bank_balances(
        banks,
        as_of_date=end_date,
        opening_date=opening_reference if start_date != end_date else None,
    )

    for bank in banks:
        closing = balances[bank.id][0]
        period_entries = [e for e in entries if e.bank_id == bank.id]
        period_deposits_all = sum(float(e.deposit or 0) for e in period_entries)
        period_withdrawals_all = sum(float(e.withdrawal or 0) for e in period_entries)
        period_deposits = sum(
            float(e.deposit or 0) for e in period_entries if _is_standard_entry(e)
        )
        period_withdrawals = sum(
            float(e.withdrawal or 0) for e in period_entries if _is_standard_entry(e)
        )

        if start_date == end_date:
            opening = closing - period_deposits_all + period_withdrawals_all
        else:
            opening = balances[bank.id][2]

        bank_balances.append(
            {
                "bank": bank,
                "opening_today": opening,
                "day_deposits": period_deposits,
                "day_withdrawals": period_withdrawals,
                "closing_balance": closing,
            }
        )
        total_closing += closing
        total_opening += opening

    return {
        "period_label": period_label,
        "start_date": start_date,
        "end_date": end_date,
        "entry_date": end_date,
        "transactions": transactions,
        "bank_balances": bank_balances,
        "total_opening_today": total_opening,
        "total_deposits": total_deposits,
        "total_withdrawals": total_withdrawals,
        "total_closing": total_closing,
        "transaction_count": len(transactions),
        "category_sections": _category_sections(transactions),
        "is_monthly": start_date != end_date,
    }


def get_bank_balance(bank: BankAccount) -> float:
    """Current balance after opening and all ledger entries."""
    balance = float(bank.opening_balance or 0)
    entries = bank.entries.order_by(
        BankLedgerEntry.entry_date.asc(), BankLedgerEntry.id.asc()
    ).all()

    for entry in entries:
        balance += float(entry.deposit or 0) - float(entry.withdrawal or 0)
    return balance


def get_bank_ledger_rows(bank: BankAccount) -> list[dict]:
    balance = float(bank.opening_balance or 0)
    rows = [{"type": "opening", "balance": balance, "bank": bank}]
    # Transfers in one extra query (the page shows each transfer's other bank).
    entries = (
        bank.entries.options(selectinload(BankLedgerEntry.transfer))
        .order_by(BankLedgerEntry.entry_date.asc(), BankLedgerEntry.id.asc())
        .all()
    )

    for entry in entries:
        balance += float(entry.deposit or 0) - float(entry.withdrawal or 0)
        rows.append({"type": "entry", "entry": entry, "balance": balance})
    return rows


def get_dashboard_stats() -> dict:
    banks = BankAccount.query.order_by(BankAccount.bank_name, BankAccount.account_number).all()
    balances = get_bank_balances(banks)
    bank_summaries = []
    total_balance = 0.0

    for bank in banks:
        balance = balances[bank.id][0]
        bank_summaries.append({"bank": bank, "balance": balance})
        total_balance += balance

    recent_transfers = (
        BankTransfer.query.order_by(
            BankTransfer.transfer_date.desc(), BankTransfer.id.desc()
        )
        .limit(5)
        .all()
    )

    return {
        "banks": bank_summaries,
        "bank_count": len(banks),
        "total_balance": total_balance,
        "recent_transfers": recent_transfers,
        "transfer_count": BankTransfer.query.count(),
    }


def bank_account_exists(bank_name: str, account_number: str | None, exclude_id: int | None = None) -> bool:
    query = BankAccount.query.filter_by(bank_name=bank_name)
    if account_number:
        query = query.filter_by(account_number=account_number)
    else:
        query = query.filter(
            (BankAccount.account_number.is_(None)) | (BankAccount.account_number == "")
        )
    if exclude_id:
        query = query.filter(BankAccount.id != exclude_id)
    return query.first() is not None


def bank_has_transfers(bank_id: int) -> bool:
    return (
        BankTransfer.query.filter(
            (BankTransfer.from_bank_id == bank_id) | (BankTransfer.to_bank_id == bank_id)
        ).first()
        is not None
    )


def _transfer_entry_notes(
    from_bank: BankAccount,
    to_bank: BankAccount,
    reference: str | None,
    notes: str | None,
    direction: str,
) -> str:
    if direction == "out":
        parts = [f"Cross-bank transfer to {to_bank.display_name}"]
    else:
        parts = [f"Cross-bank transfer from {from_bank.display_name}"]
    if reference:
        parts.append(f"Ref: {reference}")
    if notes:
        parts.append(notes)
    return " · ".join(parts)


def create_bank_transfer(
    from_bank_id: int,
    to_bank_id: int,
    transfer_date: date,
    amount: float,
    reference: str | None,
    notes: str | None,
    created_by_id: int,
) -> BankTransfer:
    if from_bank_id == to_bank_id:
        raise ValueError("From and To bank must be different.")
    if amount <= 0:
        raise ValueError("Transfer amount must be greater than zero.")

    from_bank = BankAccount.query.get(from_bank_id)
    to_bank = BankAccount.query.get(to_bank_id)
    if not from_bank or not to_bank:
        raise ValueError("Both bank accounts must exist.")

    transfer = BankTransfer(
        transfer_date=transfer_date,
        from_bank_id=from_bank_id,
        to_bank_id=to_bank_id,
        amount=amount,
        reference=reference or None,
        notes=notes or None,
        created_by_id=created_by_id,
    )
    db.session.add(transfer)
    db.session.flush()

    out_entry = BankLedgerEntry(
        bank_id=from_bank_id,
        entry_date=transfer_date,
        deposit=0,
        withdrawal=amount,
        entry_type=BankLedgerEntry.TYPE_TRANSFER_OUT,
        transfer_id=transfer.id,
        notes=_transfer_entry_notes(from_bank, to_bank, reference, notes, "out"),
        created_by_id=created_by_id,
    )
    in_entry = BankLedgerEntry(
        bank_id=to_bank_id,
        entry_date=transfer_date,
        deposit=amount,
        withdrawal=0,
        entry_type=BankLedgerEntry.TYPE_TRANSFER_IN,
        transfer_id=transfer.id,
        notes=_transfer_entry_notes(from_bank, to_bank, reference, notes, "in"),
        created_by_id=created_by_id,
    )
    db.session.add_all([out_entry, in_entry])
    return transfer


def update_bank_transfer(
    transfer: BankTransfer,
    from_bank_id: int,
    to_bank_id: int,
    transfer_date: date,
    amount: float,
    reference: str | None,
    notes: str | None,
) -> None:
    if from_bank_id == to_bank_id:
        raise ValueError("From and To bank must be different.")
    if amount <= 0:
        raise ValueError("Transfer amount must be greater than zero.")

    from_bank = BankAccount.query.get(from_bank_id)
    to_bank = BankAccount.query.get(to_bank_id)
    if not from_bank or not to_bank:
        raise ValueError("Both bank accounts must exist.")

    transfer.transfer_date = transfer_date
    transfer.from_bank_id = from_bank_id
    transfer.to_bank_id = to_bank_id
    transfer.amount = amount
    transfer.reference = reference or None
    transfer.notes = notes or None

    out_entry = next(
        (e for e in transfer.entries if e.entry_type == BankLedgerEntry.TYPE_TRANSFER_OUT),
        None,
    )
    in_entry = next(
        (e for e in transfer.entries if e.entry_type == BankLedgerEntry.TYPE_TRANSFER_IN),
        None,
    )
    if not out_entry or not in_entry:
        raise ValueError("Transfer ledger entries are missing.")

    out_entry.bank_id = from_bank_id
    out_entry.entry_date = transfer_date
    out_entry.withdrawal = amount
    out_entry.deposit = 0
    out_entry.notes = _transfer_entry_notes(from_bank, to_bank, reference, notes, "out")

    in_entry.bank_id = to_bank_id
    in_entry.entry_date = transfer_date
    in_entry.deposit = amount
    in_entry.withdrawal = 0
    in_entry.notes = _transfer_entry_notes(from_bank, to_bank, reference, notes, "in")
