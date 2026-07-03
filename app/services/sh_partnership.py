from app import db
from app.models import ShPartnerCompany, ShPurchasePartnerShare


def get_or_create_partner(name: str) -> ShPartnerCompany:
    cleaned = name.strip()
    if not cleaned:
        raise ValueError("Partner name is required.")
    existing = ShPartnerCompany.query.filter(
        db.func.lower(ShPartnerCompany.name) == cleaned.lower()
    ).first()
    if existing:
        return existing
    partner = ShPartnerCompany(name=cleaned)
    db.session.add(partner)
    db.session.flush()
    return partner


def parse_partnership_shares(form, purchase_total: float) -> list[dict]:
    if form.get("has_partnership") != "1":
        return []

    partner_ids = form.getlist("partner_id")
    partner_names = form.getlist("partner_name")
    investments = form.getlist("partner_investment")
    investment_pcts = form.getlist("partner_investment_pct")
    profit_pcts = form.getlist("partner_profit_pct")

    shares = []
    row_count = max(
        len(partner_ids),
        len(partner_names),
        len(investments),
        len(investment_pcts),
        len(profit_pcts),
    )

    for index in range(row_count):
        partner_id_raw = partner_ids[index].strip() if index < len(partner_ids) else ""
        partner_name = (partner_names[index] if index < len(partner_names) else "").strip()
        if not partner_id_raw and not partner_name:
            continue

        try:
            investment_amount = float(investments[index] if index < len(investments) else 0)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Partner row {index + 1}: enter a valid investment amount.") from exc

        try:
            investment_percent = float(
                investment_pcts[index] if index < len(investment_pcts) else 0
            )
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Partner row {index + 1}: enter a valid investment %.") from exc

        try:
            profit_percent = float(profit_pcts[index] if index < len(profit_pcts) else 0)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Partner row {index + 1}: enter a valid profit %.") from exc

        if investment_amount < 0 or investment_percent < 0 or profit_percent < 0:
            raise ValueError(f"Partner row {index + 1}: amounts and percentages cannot be negative.")

        shares.append(
            {
                "partner_id": int(partner_id_raw) if partner_id_raw else None,
                "partner_name": partner_name,
                "investment_amount": investment_amount,
                "investment_percent": investment_percent,
                "profit_percent": profit_percent,
            }
        )

    if not shares:
        raise ValueError("Add at least one partner for a partnership purchase.")

    total_investment = sum(item["investment_amount"] for item in shares)
    if purchase_total > 0 and total_investment > 0:
        for item in shares:
            if item["investment_percent"] <= 0:
                item["investment_percent"] = round(
                    (item["investment_amount"] / total_investment) * 100, 2
                )
    elif purchase_total > 0:
        for item in shares:
            if item["investment_percent"] > 0 and item["investment_amount"] <= 0:
                item["investment_amount"] = round(
                    purchase_total * (item["investment_percent"] / 100), 2
                )

    profit_total = sum(item["profit_percent"] for item in shares)
    if profit_total > 100.01:
        raise ValueError("Total profit share cannot exceed 100%.")

    return shares


def apply_partnership_from_form(purchase, form) -> None:
    if form.get("has_partnership") != "1":
        save_purchase_partnership(purchase, [])
        return
    shares = parse_partnership_shares(form, float(purchase.total_amount or 0))
    save_purchase_partnership(purchase, shares)


def save_purchase_partnership(purchase, shares: list[dict]) -> None:
    for share in list(purchase.partner_shares):
        db.session.delete(share)
    db.session.flush()

    if not shares:
        purchase.has_partnership = False
        return

    purchase.has_partnership = True
    for item in shares:
        if item.get("partner_id"):
            partner = ShPartnerCompany.query.get(item["partner_id"])
            if not partner:
                raise ValueError("Selected partner was not found.")
        else:
            partner = get_or_create_partner(item["partner_name"])

        db.session.add(
            ShPurchasePartnerShare(
                purchase_id=purchase.id,
                partner_id=partner.id,
                investment_amount=item["investment_amount"],
                investment_percent=item["investment_percent"],
                profit_percent=item["profit_percent"],
            )
        )


def get_partner_ledger_balance(partner_id: int) -> float:
    from app.models import ShLedgerEntry

    credits = (
        db.session.query(db.func.coalesce(db.func.sum(ShLedgerEntry.credit), 0))
        .filter(ShLedgerEntry.partner_company_id == partner_id)
        .scalar()
        or 0
    )
    debits = (
        db.session.query(db.func.coalesce(db.func.sum(ShLedgerEntry.debit), 0))
        .filter(ShLedgerEntry.partner_company_id == partner_id)
        .scalar()
        or 0
    )
    return float(credits) - float(debits)


def format_partnership_summary(purchase) -> str:
    if not purchase.has_partnership or not purchase.partner_shares.count():
        return "—"
    parts = []
    for share in purchase.partner_shares.all():
        parts.append(
            f"{share.partner.name}: inv {share.investment_percent:g}% / profit {share.profit_percent:g}%"
        )
    return " · ".join(parts)
