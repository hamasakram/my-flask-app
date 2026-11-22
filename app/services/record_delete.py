from sqlalchemy import exists, select

from app import db
from app.models import (
    ChemicalItem,
    ChemicalOpeningStock,
    ChemicalTransaction,
    Company,
    GlueItem,
    GlueOpeningStock,
    GlueTransaction,
    InkType,
    InventoryTransaction,
    Material,
    MaterialTransaction,
    OpeningStock,
)


def _any_rows(*checks) -> bool:
    """True if any (model, column, value) has a matching row — one round trip for all checks.

    Same answer as `model.query.filter_by(column=value).count() > 0` for each check in turn.
    """
    statement = select(
        *(exists().where(column == value).label(f"c{i}") for i, (column, value) in enumerate(checks))
    )
    return any(db.session.execute(statement).one())


def ink_company_in_use(company_id: int) -> bool:
    return _any_rows(
        (InkType.company_id, company_id),
        (OpeningStock.company_id, company_id),
        (InventoryTransaction.company_id, company_id),
    )


def materials_company_in_use(company_id: int) -> bool:
    return _any_rows(
        (Material.company_id, company_id),
        (MaterialTransaction.company_id, company_id),
    )


def glue_company_in_use(company_id: int) -> bool:
    return _any_rows(
        (GlueItem.company_id, company_id),
        (GlueOpeningStock.company_id, company_id),
        (GlueTransaction.company_id, company_id),
    )


def chemicals_company_in_use(company_id: int) -> bool:
    return _any_rows(
        (ChemicalItem.company_id, company_id),
        (ChemicalOpeningStock.company_id, company_id),
        (ChemicalTransaction.company_id, company_id),
    )


def material_in_use(material_id: int) -> bool:
    if MaterialTransaction.query.filter_by(material_id=material_id).count():
        return True
    return False


def ink_type_in_use(ink_type_id: int) -> bool:
    return _any_rows(
        (OpeningStock.ink_type_id, ink_type_id),
        (InventoryTransaction.ink_type_id, ink_type_id),
    )


def glue_item_in_use(item_id: int) -> bool:
    return _any_rows(
        (GlueOpeningStock.item_id, item_id),
        (GlueTransaction.item_id, item_id),
    )


def chemical_item_in_use(item_id: int) -> bool:
    return _any_rows(
        (ChemicalOpeningStock.item_id, item_id),
        (ChemicalTransaction.item_id, item_id),
    )
