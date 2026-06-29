from app import db
from app.models import (
    Company,
    InkType,
    InventoryTransaction,
    Material,
    MaterialOpeningStock,
    MaterialTransaction,
    OpeningStock,
    StockPurchaseReceipt,
)


def reset_ink_stock_data() -> dict:
    """Remove all ink stock data including catalog inks."""
    company_ids = [
        c.id for c in Company.query.filter_by(scope=Company.SCOPE_INK).all()
    ]
    if not company_ids:
        return {"opening": 0, "transactions": 0, "receipts": 0, "catalog": 0}

    receipts = StockPurchaseReceipt.query.filter_by(
        module=StockPurchaseReceipt.MODULE_INK
    ).delete(synchronize_session=False)
    txns = InventoryTransaction.query.filter(
        InventoryTransaction.company_id.in_(company_ids)
    ).delete(synchronize_session=False)
    opening = OpeningStock.query.filter(
        OpeningStock.company_id.in_(company_ids)
    ).delete(synchronize_session=False)
    catalog = InkType.query.filter(InkType.company_id.in_(company_ids)).delete(
        synchronize_session=False
    )
    db.session.commit()
    return {
        "opening": opening,
        "transactions": txns,
        "receipts": receipts,
        "catalog": catalog,
    }


def reset_materials_stock_data() -> dict:
    """Remove all materials stock data including catalog materials."""
    company_ids = [
        c.id for c in Company.query.filter_by(scope=Company.SCOPE_MATERIALS).all()
    ]
    if not company_ids:
        return {"opening": 0, "transactions": 0, "receipts": 0, "catalog": 0}

    receipts = StockPurchaseReceipt.query.filter_by(
        module=StockPurchaseReceipt.MODULE_MATERIALS
    ).delete(synchronize_session=False)
    txns = MaterialTransaction.query.filter(
        MaterialTransaction.company_id.in_(company_ids)
    ).delete(synchronize_session=False)
    opening = MaterialOpeningStock.query.delete(synchronize_session=False)
    catalog = Material.query.filter(Material.company_id.in_(company_ids)).delete(
        synchronize_session=False
    )
    db.session.commit()
    return {
        "opening": opening,
        "transactions": txns,
        "receipts": receipts,
        "catalog": catalog,
    }
