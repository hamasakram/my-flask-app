from app.models import MaterialOpeningStock, OpeningStock


WORKFLOW_STEPS = [
    "Enter stock purchased and used with dates from 1 June onward.",
    "After purchase/usage records are in, add opening stock.",
    "Live stock updates automatically: Opening + Purchased − Used.",
]


def opening_stock_count_ink() -> int:
    return OpeningStock.query.count()


def opening_stock_count_materials() -> int:
    return MaterialOpeningStock.query.count()


def has_opening_stock(module: str) -> bool:
    if module == "materials":
        return opening_stock_count_materials() > 0
    return opening_stock_count_ink() > 0
