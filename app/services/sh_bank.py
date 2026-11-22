from flask import g, has_request_context, session
from sqlalchemy import inspect
from sqlalchemy.exc import SQLAlchemyError

from app import db
from app.models import ShBank


def _hold_for_request(bank: ShBank | None) -> ShBank | None:
    """Keep the current bank referenced until the request ends.

    The session's identity map only holds objects weakly, so without this the bank was
    dropped after every ``get_current_sh_bank_id()`` call and ``db.session.get`` had to
    query it again (hundreds of identical SELECTs on the ledger pages). The lookup itself
    is unchanged and still reads the live row; this only stops it being thrown away.
    """
    if bank is not None and has_request_context():
        g._sh_current_bank_ref = bank
    return bank


def get_all_banks() -> list[ShBank]:
    try:
        if not inspect(db.engine).has_table("sh_banks"):
            return []
        return ShBank.query.order_by(ShBank.is_default.desc(), ShBank.name.asc()).all()
    except SQLAlchemyError:
        db.session.rollback()
        return []


def get_default_bank() -> ShBank | None:
    try:
        if not inspect(db.engine).has_table("sh_banks"):
            return None
        bank = ShBank.query.filter_by(is_default=True).first()
        if bank:
            return bank
        return ShBank.query.order_by(ShBank.id.asc()).first()
    except SQLAlchemyError:
        db.session.rollback()
        return None


def ensure_default_sh_bank() -> ShBank | None:
    """Create Askari Bank when missing so SH pages can load after deploy."""
    if not inspect(db.engine).has_table("sh_banks"):
        return None

    bank = get_default_bank()
    if bank:
        return bank

    try:
        bank = ShBank(name="Askari Bank", opening_balance=0, is_default=True)
        db.session.add(bank)
        db.session.commit()
        return bank
    except SQLAlchemyError:
        db.session.rollback()
        return get_default_bank()


def get_current_sh_bank() -> ShBank | None:
    try:
        bank_id = session.get("sh_bank_id")
        if bank_id:
            bank = db.session.get(ShBank, bank_id)
            if bank:
                return _hold_for_request(bank)

        bank = get_default_bank() or ensure_default_sh_bank()
        if bank:
            session["sh_bank_id"] = bank.id
        return _hold_for_request(bank)
    except SQLAlchemyError:
        db.session.rollback()
        return _hold_for_request(ensure_default_sh_bank())


def get_current_sh_bank_id() -> int | None:
    bank = get_current_sh_bank()
    return bank.id if bank else None


def set_current_sh_bank(bank_id: int) -> ShBank | None:
    bank = db.session.get(ShBank, bank_id)
    if bank:
        session["sh_bank_id"] = bank.id
    return _hold_for_request(bank)


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
        if not bank_id:
            bank = ensure_default_sh_bank()
            bank_id = bank.id if bank else None
        if bank_id:
            record.bank_id = bank_id
