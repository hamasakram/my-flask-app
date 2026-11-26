"""SH Traders: record of loans between the business's own accounts (banks).

Record and remembrance only — nothing here touches the bank ledgers, party ledgers or any
balance. Every entry is money moved from one account to another, labelled either a loan given
or a loan returned. From those entries we show, for each pair of accounts, how much went each
way and who still owes whom, and for each account how much it has given and taken in total.
"""

from __future__ import annotations

from datetime import date, datetime

from app import db
from app.models import ShAccountLoan, ShBank

TYPES = {
    ShAccountLoan.TYPE_LOAN: "Loan given",
    ShAccountLoan.TYPE_RETURN: "Loan returned",
}


def _clean(value) -> str:
    return " ".join(str(value or "").split())


def parse_optional_date(value: str | None) -> date | None:
    try:
        return datetime.strptime((value or "").strip(), "%Y-%m-%d").date()
    except ValueError:
        return None


def _fields_from_form(form) -> dict:
    raw_date = (form.get("loan_date") or "").strip()
    if not raw_date:
        raise ValueError("Date is required.")
    try:
        loan_date = datetime.strptime(raw_date, "%Y-%m-%d").date()
    except ValueError as exc:
        raise ValueError("Date is not valid.") from exc

    try:
        from_id = int(form.get("from_bank_id") or 0)
        to_id = int(form.get("to_bank_id") or 0)
    except ValueError as exc:
        raise ValueError("Choose both accounts.") from exc
    from_bank = db.session.get(ShBank, from_id) if from_id else None
    to_bank = db.session.get(ShBank, to_id) if to_id else None
    if not from_bank or not to_bank:
        raise ValueError("Choose both accounts.")
    if from_bank.id == to_bank.id:
        raise ValueError("Choose two different accounts.")

    raw_amount = (form.get("amount") or "").replace(",", "").strip()
    try:
        amount = float(raw_amount)
    except ValueError as exc:
        raise ValueError("Enter a valid amount.") from exc
    if amount <= 0:
        raise ValueError("Amount must be more than zero.")

    entry_type = (form.get("entry_type") or ShAccountLoan.TYPE_LOAN).strip()
    if entry_type not in TYPES:
        entry_type = ShAccountLoan.TYPE_LOAN

    return {
        "loan_date": loan_date,
        "from_bank_id": from_bank.id,
        "to_bank_id": to_bank.id,
        "amount": round(amount, 2),
        "entry_type": entry_type,
        "reference": _clean(form.get("reference"))[:150],
        "note": (form.get("note") or "").strip(),
    }


def create_loan(form, user_id: int | None) -> ShAccountLoan:
    entry = ShAccountLoan(**_fields_from_form(form), created_by_id=user_id)
    db.session.add(entry)
    db.session.flush()
    return entry


def update_loan(entry: ShAccountLoan, form) -> ShAccountLoan:
    for key, value in _fields_from_form(form).items():
        setattr(entry, key, value)
    db.session.flush()
    return entry


def list_loans(
    bank_id: int | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    entry_type: str = "",
) -> list[ShAccountLoan]:
    query = ShAccountLoan.query
    if bank_id:
        query = query.filter(
            db.or_(ShAccountLoan.from_bank_id == bank_id, ShAccountLoan.to_bank_id == bank_id)
        )
    if date_from:
        query = query.filter(ShAccountLoan.loan_date >= date_from)
    if date_to:
        query = query.filter(ShAccountLoan.loan_date <= date_to)
    if entry_type in TYPES:
        query = query.filter(ShAccountLoan.entry_type == entry_type)
    return query.order_by(ShAccountLoan.loan_date.desc(), ShAccountLoan.id.desc()).all()


def summarize(entries: list[ShAccountLoan], banks: list[ShBank]) -> dict:
    """Totals for the given entries.

    matrix[a][b]   = total money that went from account a to account b
    accounts[]     = per account: given (sent out), taken (received), net (+ = others owe it)
    pairs[]        = per pair of accounts that has entries: what each sent the other and
                     who owes whom (debtor, creditor, owed); owed 0 = settled
    """
    ids = [bank.id for bank in banks]
    name = {bank.id: bank.name for bank in banks}
    matrix = {a: {b: 0.0 for b in ids} for a in ids}
    counts = {a: {b: 0 for b in ids} for a in ids}
    loans_total = returns_total = 0.0
    for entry in entries:
        a, b = entry.from_bank_id, entry.to_bank_id
        if a not in matrix or b not in matrix:
            continue
        matrix[a][b] += float(entry.amount or 0)
        counts[a][b] += 1
        if entry.entry_type == ShAccountLoan.TYPE_RETURN:
            returns_total += float(entry.amount or 0)
        else:
            loans_total += float(entry.amount or 0)

    accounts = []
    for a in ids:
        given = round(sum(matrix[a][b] for b in ids), 2)
        taken = round(sum(matrix[b][a] for b in ids), 2)
        accounts.append(
            {"id": a, "name": name[a], "given": given, "taken": taken, "net": round(given - taken, 2)}
        )

    pairs = []
    for i, a in enumerate(ids):
        for b in ids[i + 1:]:
            if not (counts[a][b] or counts[b][a]):
                continue
            a_to_b, b_to_a = round(matrix[a][b], 2), round(matrix[b][a], 2)
            diff = round(a_to_b - b_to_a, 2)
            if diff > 0:
                debtor, creditor, owed = b, a, diff
            elif diff < 0:
                debtor, creditor, owed = a, b, -diff
            else:
                debtor = creditor = None
                owed = 0.0
            pairs.append(
                {
                    "a": a, "a_name": name[a], "b": b, "b_name": name[b],
                    "a_to_b": a_to_b, "b_to_a": b_to_a,
                    "count": counts[a][b] + counts[b][a],
                    "debtor": debtor, "debtor_name": name.get(debtor, ""),
                    "creditor": creditor, "creditor_name": name.get(creditor, ""),
                    "owed": owed,
                }
            )
    pairs.sort(key=lambda p: (-p["owed"], p["a_name"].lower(), p["b_name"].lower()))

    return {
        "matrix": {a: {b: round(v, 2) for b, v in row.items()} for a, row in matrix.items()},
        "counts": counts,
        "accounts": accounts,
        "pairs": pairs,
        "total_moved": round(loans_total + returns_total, 2),
        "loans_total": round(loans_total, 2),
        "returns_total": round(returns_total, 2),
        "open_total": round(sum(p["owed"] for p in pairs), 2),
        "count": len(entries),
    }
