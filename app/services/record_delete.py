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


def ink_company_in_use(company_id: int) -> bool:
    if InkType.query.filter_by(company_id=company_id).count():
        return True
    if OpeningStock.query.filter_by(company_id=company_id).count():
        return True
    if InventoryTransaction.query.filter_by(company_id=company_id).count():
        return True
    return False


def materials_company_in_use(company_id: int) -> bool:
    if Material.query.filter_by(company_id=company_id).count():
        return True
    if MaterialTransaction.query.filter_by(company_id=company_id).count():
        return True
    return False


def glue_company_in_use(company_id: int) -> bool:
    if GlueItem.query.filter_by(company_id=company_id).count():
        return True
    if GlueOpeningStock.query.filter_by(company_id=company_id).count():
        return True
    if GlueTransaction.query.filter_by(company_id=company_id).count():
        return True
    return False


def chemicals_company_in_use(company_id: int) -> bool:
    if ChemicalItem.query.filter_by(company_id=company_id).count():
        return True
    if ChemicalOpeningStock.query.filter_by(company_id=company_id).count():
        return True
    if ChemicalTransaction.query.filter_by(company_id=company_id).count():
        return True
    return False


def material_in_use(material_id: int) -> bool:
    if MaterialTransaction.query.filter_by(material_id=material_id).count():
        return True
    return False


def ink_type_in_use(ink_type_id: int) -> bool:
    from app.models import InkType, InventoryTransaction, OpeningStock

    if OpeningStock.query.filter_by(ink_type_id=ink_type_id).count():
        return True
    if InventoryTransaction.query.filter_by(ink_type_id=ink_type_id).count():
        return True
    return False


def glue_item_in_use(item_id: int) -> bool:
    if GlueOpeningStock.query.filter_by(item_id=item_id).count():
        return True
    if GlueTransaction.query.filter_by(item_id=item_id).count():
        return True
    return False


def chemical_item_in_use(item_id: int) -> bool:
    if ChemicalOpeningStock.query.filter_by(item_id=item_id).count():
        return True
    if ChemicalTransaction.query.filter_by(item_id=item_id).count():
        return True
    return False
