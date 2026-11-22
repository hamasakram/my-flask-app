from app import db
from app.models import HomeLedgerEntry, HomeParty


def _apply_entry(party: HomeParty, balance: float, given, received) -> float:
    given = float(given or 0)
    received = float(received or 0)
    if party.balance_kind == HomeParty.KIND_TO_PAY:
        return balance - given + received
    return balance - received + given


def get_party_balance(party: HomeParty) -> float:
    """Current outstanding balance after opening and all ledger entries."""
    balance = float(party.opening_amount or 0)
    entries = party.entries.order_by(
        HomeLedgerEntry.entry_date.asc(), HomeLedgerEntry.id.asc()
    ).all()

    for entry in entries:
        balance = _apply_entry(party, balance, entry.given, entry.received)
    return balance


def get_party_balances(parties) -> dict:
    """{party_id: (balance, entry_count)} for many parties from ONE query.

    Same arithmetic, in the same (entry_date, id) order, as get_party_balance() per party.
    """
    by_id = {party.id: party for party in parties}
    state = {party.id: [float(party.opening_amount or 0), 0] for party in parties}
    if not state:
        return {}
    rows = db.session.query(
        HomeLedgerEntry.party_id, HomeLedgerEntry.given, HomeLedgerEntry.received
    ).order_by(HomeLedgerEntry.entry_date.asc(), HomeLedgerEntry.id.asc())
    for party_id, given, received in rows:
        current = state.get(party_id)
        if current is None:
            continue
        current[0] = _apply_entry(by_id[party_id], current[0], given, received)
        current[1] += 1
    return {party_id: (value[0], value[1]) for party_id, value in state.items()}


def get_party_ledger_rows(party: HomeParty) -> list[dict]:
    balance = float(party.opening_amount or 0)
    rows = [{"type": "opening", "balance": balance, "party": party}]
    entries = party.entries.order_by(
        HomeLedgerEntry.entry_date.asc(), HomeLedgerEntry.id.asc()
    ).all()

    for entry in entries:
        balance = _apply_entry(party, balance, entry.given, entry.received)
        rows.append({"type": "entry", "entry": entry, "balance": balance})
    return rows


def get_dashboard_stats() -> dict:
    parties = HomeParty.query.order_by(HomeParty.name).all()
    balances = get_party_balances(parties)
    party_summaries = []
    total_to_pay = 0.0
    total_to_receive = 0.0

    for party in parties:
        balance = balances[party.id][0]
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
