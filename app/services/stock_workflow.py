from app.models import ChemicalOpeningStock, GlueOpeningStock, MaterialOpeningStock, OpeningStock


WORKFLOW_STEPS = [
    "Add companies and register inks in the catalog.",
    "Set opening stock and receive new ink — both go to stored (backup) inventory.",
    "When ink is needed on press, issue quantity from stored to in-use.",
    "Record daily usage against in-use stock only.",
    "Live inventory shows stored backup and in-use quantities; dashboard shows in-use only.",
]


def opening_stock_count_ink() -> int:
    return OpeningStock.query.count()


def opening_stock_count_materials() -> int:
    return MaterialOpeningStock.query.count()


def opening_stock_count_glue() -> int:
    return GlueOpeningStock.query.count()


def opening_stock_count_chemicals() -> int:
    return ChemicalOpeningStock.query.count()


def has_opening_stock(module: str) -> bool:
    if module == "materials":
        return opening_stock_count_materials() > 0
    if module == "glue":
        return opening_stock_count_glue() > 0
    if module == "chemicals":
        return opening_stock_count_chemicals() > 0
    return opening_stock_count_ink() > 0
