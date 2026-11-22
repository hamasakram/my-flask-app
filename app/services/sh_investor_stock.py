"""Investor stock purchase / sale / payment records — isolated from ledgers."""

from datetime import date

from sqlalchemy import func, select

from app import db
from app.models import (
    ShInvestorPurchase,
    ShInvestorSale,
    ShInvestorSalePayment,
    ShPartnerCompany,
)
from app.services.sh_bank import filter_by_bank, get_current_sh_bank


def line_total(quantity: float, rate: float) -> float:
    if not quantity or not rate:
        return 0.0
    return float(quantity) * float(rate)


def scoped_purchases_query():
    return filter_by_bank(ShInvestorPurchase.query, ShInvestorPurchase).order_by(
        ShInvestorPurchase.purchase_date.desc(), ShInvestorPurchase.id.desc()
    )


def scoped_sales_query():
    return filter_by_bank(ShInvestorSale.query, ShInvestorSale).order_by(
        ShInvestorSale.sale_date.desc(), ShInvestorSale.id.desc()
    )


def scoped_payments_query():
    return filter_by_bank(ShInvestorSalePayment.query, ShInvestorSalePayment).order_by(
        ShInvestorSalePayment.payment_date.desc(), ShInvestorSalePayment.id.desc()
    )


def next_investor_sale_number() -> str:
    year = date.today().year
    prefix = f"IS-{year}-"
    last = (
        filter_by_bank(ShInvestorSale.query, ShInvestorSale)
        .filter(ShInvestorSale.invoice_number.like(f"{prefix}%"))
        .order_by(ShInvestorSale.id.desc())
        .first()
    )
    if not last:
        return f"{prefix}0001"
    try:
        seq = int(last.invoice_number.split("-")[-1]) + 1
    except (ValueError, IndexError):
        seq = 1
    return f"{prefix}{seq:04d}"


def get_purchase_sold_kg(purchase_id: int, exclude_sale_id: int | None = None) -> float:
    query = db.session.query(func.coalesce(func.sum(ShInvestorSale.quantity_kg), 0)).filter(
        ShInvestorSale.purchase_id == purchase_id
    )
    if exclude_sale_id:
        query = query.filter(ShInvestorSale.id != exclude_sale_id)
    total = query.scalar()
    return float(total or 0)


def get_purchase_remaining_kg(
    purchase: ShInvestorPurchase, exclude_sale_id: int | None = None
) -> float:
    sold = get_purchase_sold_kg(purchase.id, exclude_sale_id=exclude_sale_id)
    return max(0.0, float(purchase.quantity_kg or 0) - sold)


def get_sale_paid(sale_id: int) -> float:
    total = (
        db.session.query(func.coalesce(func.sum(ShInvestorSalePayment.amount_received), 0))
        .filter(ShInvestorSalePayment.sale_id == sale_id)
        .scalar()
    )
    return float(total or 0)


def get_sale_remaining(sale: ShInvestorSale) -> float:
    paid = get_sale_paid(sale.id)
    return max(0.0, float(sale.total_amount or 0) - paid)


def build_purchase_from_form(form, purchase_id: int | None = None) -> dict:
    purchase_date = form.get("purchase_date")
    partner_id = form.get("partner_company_id", type=int)
    material_name = form.get("material_name", "").strip()
    size = form.get("size", "").strip()
    quantity_kg = form.get("quantity_kg", type=float)
    rate_per_kg = form.get("rate_per_kg", type=float)
    investment_amount = form.get("investment_amount", type=float)
    supplier_name = form.get("supplier_name", "").strip()
    notes = form.get("notes", "").strip()

    if not purchase_date:
        raise ValueError("Purchase date is required.")
    if not partner_id:
        raise ValueError("Select the investor who paid for this stock.")
    if not material_name:
        raise ValueError("Material name is required.")
    if not quantity_kg or quantity_kg <= 0:
        raise ValueError("Quantity (KG) must be greater than zero.")
    if not rate_per_kg or rate_per_kg <= 0:
        raise ValueError("Rate per KG must be greater than zero.")

    total_amount = line_total(quantity_kg, rate_per_kg)
    if investment_amount is None or investment_amount <= 0:
        investment_amount = total_amount
    if investment_amount > total_amount + 0.01:
        raise ValueError("Investment amount cannot exceed purchase total.")

    if purchase_id:
        sold_kg = get_purchase_sold_kg(purchase_id)
        if quantity_kg < sold_kg - 0.001:
            raise ValueError(
                f"Quantity cannot be less than already sold ({sold_kg:,.3f} KG linked to sales)."
            )

    if not ShPartnerCompany.query.get(partner_id):
        raise ValueError("Selected investor not found.")

    return {
        "purchase_date": purchase_date,
        "partner_company_id": partner_id,
        "material_name": material_name,
        "size": size,
        "quantity_kg": quantity_kg,
        "rate_per_kg": rate_per_kg,
        "total_amount": total_amount,
        "investment_amount": investment_amount,
        "supplier_name": supplier_name or None,
        "notes": notes or None,
    }


def apply_purchase_fields(purchase: ShInvestorPurchase, data: dict, purchase_date) -> None:
    from datetime import datetime

    purchase.purchase_date = (
        purchase_date
        if hasattr(purchase_date, "year")
        else datetime.strptime(str(purchase_date), "%Y-%m-%d").date()
    )
    purchase.partner_company_id = data["partner_company_id"]
    purchase.material_name = data["material_name"]
    purchase.size = data["size"]
    purchase.quantity_kg = data["quantity_kg"]
    purchase.rate_per_kg = data["rate_per_kg"]
    purchase.total_amount = data["total_amount"]
    purchase.investment_amount = data["investment_amount"]
    purchase.supplier_name = data["supplier_name"]
    purchase.notes = data["notes"]


def build_sale_from_form(form, exclude_sale_id: int | None = None) -> dict:
    sale_date = form.get("sale_date")
    invoice_number = form.get("invoice_number", "").strip()
    partner_id = form.get("partner_company_id", type=int)
    client_id = form.get("client_company_id", type=int)
    purchase_id = form.get("purchase_id", type=int) or None
    material_name = form.get("material_name", "").strip()
    size = form.get("size", "").strip()
    quantity_kg = form.get("quantity_kg", type=float)
    rate_per_kg = form.get("rate_per_kg", type=float)
    notes = form.get("notes", "").strip()

    if not sale_date:
        raise ValueError("Sale date is required.")
    if not partner_id:
        raise ValueError("Select the investor this sale belongs to.")
    if not client_id:
        raise ValueError("Select the party (client) sold to.")
    if not material_name:
        raise ValueError("Material name is required.")
    if not quantity_kg or quantity_kg <= 0:
        raise ValueError("Quantity sold (KG) must be greater than zero.")
    if not rate_per_kg or rate_per_kg <= 0:
        raise ValueError("Sale rate per KG must be greater than zero.")

    if purchase_id:
        purchase = ShInvestorPurchase.query.get(purchase_id)
        if not purchase:
            raise ValueError("Linked purchase record not found.")
        if purchase.partner_company_id != partner_id:
            raise ValueError("Selected purchase does not belong to this investor.")
        remaining_kg = get_purchase_remaining_kg(purchase, exclude_sale_id=exclude_sale_id)
        if quantity_kg > remaining_kg + 0.001:
            raise ValueError(
                f"Quantity exceeds remaining stock ({remaining_kg:,.3f} KG left on that purchase)."
            )

    total_amount = line_total(quantity_kg, rate_per_kg)
    return {
        "sale_date": sale_date,
        "invoice_number": invoice_number or next_investor_sale_number(),
        "partner_company_id": partner_id,
        "client_company_id": client_id,
        "purchase_id": purchase_id,
        "material_name": material_name,
        "size": size,
        "quantity_kg": quantity_kg,
        "rate_per_kg": rate_per_kg,
        "total_amount": total_amount,
        "notes": notes or None,
    }


def apply_sale_fields(sale: ShInvestorSale, data: dict, sale_date, *, skip_purchase_check: bool = False) -> None:
    from datetime import datetime

    if not skip_purchase_check and data.get("purchase_id"):
        purchase = ShInvestorPurchase.query.get(data["purchase_id"])
        if purchase:
            other_sold = (
                db.session.query(func.coalesce(func.sum(ShInvestorSale.quantity_kg), 0))
                .filter(
                    ShInvestorSale.purchase_id == purchase.id,
                    ShInvestorSale.id != sale.id,
                )
                .scalar()
            )
            remaining = float(purchase.quantity_kg or 0) - float(other_sold or 0)
            if data["quantity_kg"] > remaining + 0.001:
                raise ValueError(
                    f"Quantity exceeds remaining stock ({remaining:,.3f} KG left on that purchase)."
                )

    sale.sale_date = (
        sale_date
        if hasattr(sale_date, "year")
        else datetime.strptime(str(sale_date), "%Y-%m-%d").date()
    )
    sale.invoice_number = data["invoice_number"]
    sale.partner_company_id = data["partner_company_id"]
    sale.client_company_id = data["client_company_id"]
    sale.purchase_id = data.get("purchase_id")
    sale.material_name = data["material_name"]
    sale.size = data["size"]
    sale.quantity_kg = data["quantity_kg"]
    sale.rate_per_kg = data["rate_per_kg"]
    sale.total_amount = data["total_amount"]
    sale.notes = data["notes"]


def build_payment_from_form(form, exclude_payment_id: int | None = None) -> dict:
    payment_date = form.get("payment_date")
    partner_id = form.get("partner_company_id", type=int)
    client_id = form.get("client_company_id", type=int)
    sale_id = form.get("sale_id", type=int)
    amount_received = form.get("amount_received", type=float)
    notes = form.get("notes", "").strip()

    if not payment_date:
        raise ValueError("Payment date is required.")
    if not partner_id:
        raise ValueError("Select the investor.")
    if not client_id:
        raise ValueError("Select the party who paid.")
    if not sale_id:
        raise ValueError("Select the investor sale invoice this payment applies to.")
    if not amount_received or amount_received <= 0:
        raise ValueError("Amount received must be greater than zero.")

    sale = ShInvestorSale.query.get(sale_id)
    if not sale:
        raise ValueError("Sale invoice not found.")
    if sale.partner_company_id != partner_id:
        raise ValueError("Selected invoice does not belong to this investor.")
    if sale.client_company_id != client_id:
        raise ValueError("Selected invoice does not belong to this party.")

    paid = get_sale_paid(sale.id)
    if exclude_payment_id:
        old = ShInvestorSalePayment.query.get(exclude_payment_id)
        if old and old.sale_id == sale.id:
            paid -= float(old.amount_received or 0)
    remaining = max(0.0, float(sale.total_amount or 0) - paid)
    if amount_received > remaining + 0.01:
        raise ValueError(
            f"Amount exceeds invoice balance (₨ {remaining:,.2f} remaining on {sale.invoice_number})."
        )

    return {
        "payment_date": payment_date,
        "partner_company_id": partner_id,
        "client_company_id": client_id,
        "sale_id": sale_id,
        "amount_received": amount_received,
        "notes": notes or None,
    }


def apply_payment_fields(payment: ShInvestorSalePayment, data: dict, payment_date) -> None:
    from datetime import datetime

    sale = ShInvestorSale.query.get(data["sale_id"])
    if sale:
        other_paid = (
            db.session.query(func.coalesce(func.sum(ShInvestorSalePayment.amount_received), 0))
            .filter(
                ShInvestorSalePayment.sale_id == sale.id,
                ShInvestorSalePayment.id != payment.id,
            )
            .scalar()
        )
        remaining = float(sale.total_amount or 0) - float(other_paid or 0)
        if data["amount_received"] > remaining + 0.01:
            raise ValueError(
                f"Amount exceeds invoice balance (₨ {remaining:,.2f} remaining on {sale.invoice_number})."
            )

    payment.payment_date = (
        payment_date
        if hasattr(payment_date, "year")
        else datetime.strptime(str(payment_date), "%Y-%m-%d").date()
    )
    payment.partner_company_id = data["partner_company_id"]
    payment.client_company_id = data["client_company_id"]
    payment.sale_id = data["sale_id"]
    payment.amount_received = data["amount_received"]
    payment.notes = data["notes"]


def _sold_kg_by_purchase() -> dict[int, float]:
    """``get_purchase_sold_kg`` for every purchase of the current bank in one statement.

    Each value is the same ``coalesce(sum(quantity_kg), 0)`` over the same rows as the
    per-purchase query (a correlated subquery per purchase), only without a round trip each.
    """
    sold = (
        select(func.coalesce(func.sum(ShInvestorSale.quantity_kg), 0))
        .where(ShInvestorSale.purchase_id == ShInvestorPurchase.id)
        .correlate(ShInvestorPurchase)
        .scalar_subquery()
    )
    query = filter_by_bank(db.session.query(ShInvestorPurchase.id, sold), ShInvestorPurchase)
    return {purchase_id: float(total or 0) for purchase_id, total in query}


def _paid_by_sale() -> dict[int, float]:
    """``get_sale_paid`` for every sale of the current bank in one statement."""
    paid = (
        select(func.coalesce(func.sum(ShInvestorSalePayment.amount_received), 0))
        .where(ShInvestorSalePayment.sale_id == ShInvestorSale.id)
        .correlate(ShInvestorSale)
        .scalar_subquery()
    )
    query = filter_by_bank(db.session.query(ShInvestorSale.id, paid), ShInvestorSale)
    return {sale_id: float(total or 0) for sale_id, total in query}


def get_partner_summaries(
    partners: list[ShPartnerCompany],
    purchases: list[ShInvestorPurchase] | None = None,
    sales: list[ShInvestorSale] | None = None,
    paid_by_sale: dict[int, float] | None = None,
) -> list[dict]:
    """Per-investor totals.

    ``purchases``/``sales`` may be this request's ``scoped_purchases_query().all()`` /
    ``scoped_sales_query().all()`` and ``paid_by_sale`` the matching ``get_sale_paid`` values;
    each partner's rows are then picked from them (same rows, same order) instead of being
    queried again per partner.
    """
    summaries = []
    for partner in partners:
        if purchases is None:
            partner_purchases = (
                scoped_purchases_query()
                .filter(ShInvestorPurchase.partner_company_id == partner.id)
                .all()
            )
        else:
            partner_purchases = [p for p in purchases if p.partner_company_id == partner.id]
        if sales is None:
            partner_sales = (
                scoped_sales_query()
                .filter(ShInvestorSale.partner_company_id == partner.id)
                .all()
            )
        else:
            partner_sales = [s for s in sales if s.partner_company_id == partner.id]
        invested = sum(float(p.investment_amount or 0) for p in partner_purchases)
        billed = sum(float(s.total_amount or 0) for s in partner_sales)
        collected = sum(_sale_paid(s, paid_by_sale) for s in partner_sales)
        outstanding = billed - collected
        summaries.append(
            {
                "partner": partner,
                "purchase_count": len(partner_purchases),
                "sale_count": len(partner_sales),
                "invested": invested,
                "billed": billed,
                "collected": collected,
                "outstanding": outstanding,
            }
        )
    return summaries


def _sale_paid(sale: ShInvestorSale, paid_by_sale: dict[int, float] | None) -> float:
    if paid_by_sale is not None and sale.id in paid_by_sale:
        return paid_by_sale[sale.id]
    return get_sale_paid(sale.id)


def _remaining_kg(purchase: ShInvestorPurchase, sold_by_purchase: dict[int, float]) -> float:
    """``get_purchase_remaining_kg`` using the preloaded sold totals."""
    sold = sold_by_purchase.get(purchase.id)
    if sold is None:
        sold = get_purchase_sold_kg(purchase.id)
    return max(0.0, float(purchase.quantity_kg or 0) - sold)


def build_page_context() -> dict:
    purchases = scoped_purchases_query().all()
    sales = scoped_sales_query().all()
    payments = scoped_payments_query().all()
    partners = ShPartnerCompany.query.order_by(ShPartnerCompany.name).all()
    sold_by_purchase = _sold_kg_by_purchase()
    paid_by_sale = _paid_by_sale()

    purchase_rows = []
    remaining_by_purchase = {}
    for purchase in purchases:
        remaining_kg = _remaining_kg(purchase, sold_by_purchase)
        remaining_by_purchase[purchase.id] = remaining_kg
        purchase_rows.append(
            {
                "purchase": purchase,
                "sold_kg": float(purchase.quantity_kg or 0) - remaining_kg,
                "remaining_kg": remaining_kg,
            }
        )

    sale_rows = []
    for sale in sales:
        paid = _sale_paid(sale, paid_by_sale)
        sale_rows.append(
            {
                "sale": sale,
                "paid": paid,
                "remaining": max(0.0, float(sale.total_amount or 0) - paid),
            }
        )

    partner_purchase_map = {}
    partner_sale_map = {}
    client_sale_map = {}

    for purchase in purchases:
        partner_purchase_map.setdefault(purchase.partner_company_id, []).append(
            {
                "id": purchase.id,
                "date": purchase.purchase_date.strftime("%d-%m-%Y"),
                "material": purchase.material_name,
                "remaining_kg": remaining_by_purchase[purchase.id],
                "investment": float(purchase.investment_amount or 0),
            }
        )

    for row in sale_rows:
        sale = row["sale"]
        partner_sale_map.setdefault(sale.partner_company_id, []).append(
            {
                "id": sale.id,
                "number": sale.invoice_number,
                "client_id": sale.client_company_id,
                "date": sale.sale_date.strftime("%d-%m-%Y"),
                "material": sale.material_name,
                "total": float(sale.total_amount or 0),
                "remaining": row["remaining"],
            }
        )
        client_sale_map.setdefault(sale.client_company_id, []).append(
            {
                "id": sale.id,
                "number": sale.invoice_number,
                "partner_id": sale.partner_company_id,
                "remaining": row["remaining"],
            }
        )

    return {
        "purchase_rows": purchase_rows,
        "sale_rows": sale_rows,
        "payments": payments,
        "partners": partners,
        "partner_summaries": get_partner_summaries(
            partners, purchases=purchases, sales=sales, paid_by_sale=paid_by_sale
        ),
        "partner_purchase_map": partner_purchase_map,
        "partner_sale_map": partner_sale_map,
        "client_sale_map": client_sale_map,
        "next_sale_number": next_investor_sale_number(),
        "current_bank": get_current_sh_bank(),
    }
