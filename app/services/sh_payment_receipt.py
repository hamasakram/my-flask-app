from datetime import datetime

from app import db
from app.models import ShPaymentReceipt
from app.services.sh_bank import filter_by_bank, get_current_sh_bank_id


def next_payment_receipt_number() -> str:
    year = datetime.now().year
    prefix = f"PR-{year}-"
    bank_id = get_current_sh_bank_id()
    query = ShPaymentReceipt.query.filter(
        ShPaymentReceipt.receipt_number.like(f"{prefix}%")
    )
    if bank_id:
        query = query.filter(ShPaymentReceipt.bank_id == bank_id)
    latest = query.order_by(ShPaymentReceipt.id.desc()).first()
    if latest:
        try:
            seq = int(latest.receipt_number.rsplit("-", 1)[-1]) + 1
        except ValueError:
            seq = latest.id + 1
    else:
        seq = 1
    return f"{prefix}{seq:04d}"


def get_payment_receipts() -> list[ShPaymentReceipt]:
    query = filter_by_bank(ShPaymentReceipt.query, ShPaymentReceipt)
    return query.order_by(
        ShPaymentReceipt.receipt_date.desc(), ShPaymentReceipt.id.desc()
    ).all()


def get_last_receipt_for_client(client_id: int) -> ShPaymentReceipt | None:
    bank_id = get_current_sh_bank_id()
    query = ShPaymentReceipt.query.filter(
        ShPaymentReceipt.client_company_id == client_id
    )
    if bank_id:
        query = query.filter(ShPaymentReceipt.bank_id == bank_id)
    return query.order_by(
        ShPaymentReceipt.receipt_date.desc(), ShPaymentReceipt.id.desc()
    ).first()


def suggest_total_received(client_id: int, amount_received: float) -> float:
    last = get_last_receipt_for_client(client_id)
    previous_total = float(last.total_received) if last else 0.0
    return previous_total + float(amount_received)
