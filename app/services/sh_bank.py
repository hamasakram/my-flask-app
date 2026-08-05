from flask import session

from app import db
from app.models import ShBank


def get_all_banks() -> list[ShBank]:
    return ShBank.query.order_by(ShBank.is_default.desc(), ShBank.name).all()


def get_default_bank() -> ShBank | None:
    bank = ShBank.query.filter_by(is_default=True).first()
    if bank:
        return bank
    return ShBank.query.order_by(ShBank.id).first()


def get_current_sh_bank() -> ShBank | None:
    bank_id = session.get("sh_bank_id")
    if bank_id:
        bank = db.session.get(ShBank, bank_id)
        if bank:
            return bank
    bank = get_default_bank()
    if bank:
        session["sh_bank_id"] = bank.id
    return bank


def get_current_sh_bank_id() -> int | None:
    bank = get_current_sh_bank()
    return bank.id if bank else None


def set_current_sh_bank(bank_id: int) -> ShBank | None:
    bank = db.session.get(ShBank, bank_id)
    if bank:
        session["sh_bank_id"] = bank.id
    return bank


def filter_by_bank(query, model):
    """Scope a SQLAlchemy query to the current SH bank."""
    bank_id = get_current_sh_bank_id()
    if bank_id is None:
        return query.filter(False)
    if hasattr(model, "bank_id"):
        return query.filter(model.bank_id == bank_id)
    return query


def ensure_bank_on_create(record) -> None:
    """Assign current bank to a new record if not set."""
    if hasattr(record, "bank_id") and not record.bank_id:
        bank_id = get_current_sh_bank_id()
        if bank_id:
            record.bank_id = bank_id
