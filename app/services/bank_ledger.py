from app.models import BankAccount, BankLedgerEntry


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
    entries = bank.entries.order_by(
        BankLedgerEntry.entry_date.asc(), BankLedgerEntry.id.asc()
    ).all()

    for entry in entries:
        balance += float(entry.deposit or 0) - float(entry.withdrawal or 0)
        rows.append({"type": "entry", "entry": entry, "balance": balance})
    return rows


def get_dashboard_stats() -> dict:
    banks = BankAccount.query.order_by(BankAccount.bank_name, BankAccount.account_number).all()
    bank_summaries = []
    total_balance = 0.0

    for bank in banks:
        balance = get_bank_balance(bank)
        bank_summaries.append({"bank": bank, "balance": balance})
        total_balance += balance

    return {
        "banks": bank_summaries,
        "bank_count": len(banks),
        "total_balance": total_balance,
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
