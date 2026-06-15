from app.models import HomeLedgerEntry, HomeParty


def get_party_balance(party: HomeParty) -> float:
    """Current outstanding balance after opening and all ledger entries."""
    balance = float(party.opening_amount or 0)
    entries = party.entries.order_by(
        HomeLedgerEntry.entry_date.asc(), HomeLedgerEntry.id.asc()
    ).all()

    for entry in entries:
        given = float(entry.given or 0)
        received = float(entry.received or 0)
        if party.balance_kind == HomeParty.KIND_TO_PAY:
            balance = balance - given + received
        else:
            balance = balance - received + given
    return balance


def get_party_ledger_rows(party: HomeParty) -> list[dict]:
    balance = float(party.opening_amount or 0)
    rows = [{"type": "opening", "balance": balance, "party": party}]
    entries = party.entries.order_by(
        HomeLedgerEntry.entry_date.asc(), HomeLedgerEntry.id.asc()
    ).all()

    for entry in entries:
        given = float(entry.given or 0)
        received = float(entry.received or 0)
        if party.balance_kind == HomeParty.KIND_TO_PAY:
            balance = balance - given + received
        else:
            balance = balance - received + given
        rows.append({"type": "entry", "entry": entry, "balance": balance})
    return rows


def get_dashboard_stats() -> dict:
    parties = HomeParty.query.order_by(HomeParty.name).all()
    party_summaries = []
    total_to_pay = 0.0
    total_to_receive = 0.0

    for party in parties:
        balance = get_party_balance(party)
        party_summaries.append({"party": party, "balance": balance})
        if party.balance_kind == HomeParty.KIND_TO_PAY:
            total_to_pay += balance
        else:
            total_to_receive += balance

    return {
        "parties": party_summaries,
        "party_count": len(parties),
        "total_to_pay": total_to_pay,
        "total_to_receive": total_to_receive,
    }
