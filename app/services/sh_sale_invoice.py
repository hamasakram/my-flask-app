from datetime import datetime

from app import db


def next_sale_invoice_number() -> str:
    from app.models import ShSaleInvoice

    year = datetime.now().year
    prefix = f"{year}-"
    latest = (
        ShSaleInvoice.query.filter(ShSaleInvoice.invoice_number.like(f"{prefix}%"))
        .order_by(ShSaleInvoice.id.desc())
        .first()
    )
    if latest:
        try:
            seq = int(latest.invoice_number.split("-", 1)[1]) + 1
        except (IndexError, ValueError):
            seq = latest.id + 1
    else:
        seq = 1
    return f"{prefix}{seq}"


def calculate_line_total(net_weight: float, unit_price: float) -> float:
    return round(float(net_weight or 0) * float(unit_price or 0))


def parse_invoice_lines(form) -> list[dict]:
    items = form.getlist("line_item")
    sizes = form.getlist("line_size")
    qtys = form.getlist("line_qty")
    qty_units = form.getlist("line_qty_unit")
    gross_weights = form.getlist("line_gross_weight")
    net_weights = form.getlist("line_net_weight")
    unit_prices = form.getlist("line_unit_price")

    lines = []
    for index in range(len(items)):
        item_name = (items[index] or "").strip()
        if not item_name:
            continue

        try:
            qty = float(qtys[index] if index < len(qtys) else 0)
            gross_weight = float(gross_weights[index] if index < len(gross_weights) else 0)
            net_weight = float(net_weights[index] if index < len(net_weights) else 0)
            unit_price = float(unit_prices[index] if index < len(unit_prices) else 0)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Line {index + 1}: enter valid numbers for qty, weights, and price.") from exc

        if net_weight <= 0 or unit_price <= 0:
            raise ValueError(f"Line {index + 1}: net weight and unit price must be greater than zero.")

        lines.append(
            {
                "line_number": len(lines) + 1,
                "item_name": item_name,
                "size": (sizes[index] if index < len(sizes) else "").strip(),
                "qty": qty,
                "qty_unit": (qty_units[index] if index < len(qty_units) else "Roll/Reel").strip()
                or "Roll/Reel",
                "gross_weight": gross_weight,
                "net_weight": net_weight,
                "unit_price": unit_price,
                "line_total": calculate_line_total(net_weight, unit_price),
            }
        )

    if not lines:
        raise ValueError("Add at least one invoice line item.")

    return lines


def save_invoice_lines(invoice, lines: list[dict]) -> float:
    from app.models import ShSaleInvoiceLine

    for line in list(invoice.lines):
        db.session.delete(line)
    db.session.flush()

    total = 0.0
    for line_data in lines:
        db.session.add(
            ShSaleInvoiceLine(
                invoice_id=invoice.id,
                line_number=line_data["line_number"],
                item_name=line_data["item_name"],
                size=line_data["size"],
                qty=line_data["qty"],
                qty_unit=line_data["qty_unit"],
                gross_weight=line_data["gross_weight"],
                net_weight=line_data["net_weight"],
                unit_price=line_data["unit_price"],
                line_total=line_data["line_total"],
            )
        )
        total += line_data["line_total"]
    return total


def compute_current_balance(previous_balance: float, total_amount: float, balance_type: str = "DR") -> tuple[float, str]:
    """Current balance = previous + invoice total (debit style ledger)."""
    current = float(previous_balance or 0) + float(total_amount or 0)
    return current, balance_type or "DR"
