from app import db
from app.models import (
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
    receipts = StockPurchaseReceipt.query.filter_by(
        module=StockPurchaseReceipt.MODULE_INK
    ).delete(synchronize_session=False)
    txns = InventoryTransaction.query.delete(synchronize_session=False)
    opening = OpeningStock.query.delete(synchronize_session=False)
    catalog = InkType.query.delete(synchronize_session=False)
    db.session.commit()
    return {
        "opening": opening,
        "transactions": txns,
        "receipts": receipts,
        "catalog": catalog,
    }


def reset_materials_stock_data() -> dict:
    """Remove all materials stock data including catalog materials."""
    receipts = StockPurchaseReceipt.query.filter_by(
        module=StockPurchaseReceipt.MODULE_MATERIALS
    ).delete(synchronize_session=False)
    txns = MaterialTransaction.query.delete(synchronize_session=False)
    opening = MaterialOpeningStock.query.delete(synchronize_session=False)
    catalog = Material.query.delete(synchronize_session=False)
    db.session.commit()
    return {
        "opening": opening,
        "transactions": txns,
        "receipts": receipts,
        "catalog": catalog,
    }
