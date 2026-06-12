from app.models import (
    ChemicalItem,
    ChemicalOpeningStock,
    ChemicalTransaction,
    GlueItem,
    GlueOpeningStock,
    GlueTransaction,
)
from app.services.product_stock import (
    calculate_live_stock_generic,
    calculate_used_from_left_generic,
    get_current_stock_generic,
    get_dashboard_stats_generic,
    get_or_create_item,
)

GLUE_THRESHOLD_KEY = "default_glue_low_stock_threshold"
CHEMICAL_THRESHOLD_KEY = "default_chemical_low_stock_threshold"


def get_or_create_glue(company_id: int, name: str, unit_type: str = "Kg"):
    return get_or_create_item(GlueItem, company_id, name, unit_type=unit_type)


def get_or_create_chemical(company_id: int, name: str, unit_type: str = "Kg"):
    return get_or_create_item(ChemicalItem, company_id, name, unit_type=unit_type)


def glue_live_stock(company_id=None, item_id=None):
    return calculate_live_stock_generic(
        GlueItem,
        GlueOpeningStock,
        GlueTransaction,
        "item_id",
        GLUE_THRESHOLD_KEY,
        company_id,
        item_id,
    )


def chemical_live_stock(company_id=None, item_id=None):
    return calculate_live_stock_generic(
        ChemicalItem,
        ChemicalOpeningStock,
        ChemicalTransaction,
        "item_id",
        CHEMICAL_THRESHOLD_KEY,
        company_id,
        item_id,
    )


def glue_used_from_left(company_id, item_id, quantity_left):
    return calculate_used_from_left_generic(
        GlueItem,
        GlueOpeningStock,
        GlueTransaction,
        "item_id",
        GLUE_THRESHOLD_KEY,
        company_id,
        item_id,
        quantity_left,
    )


def chemical_used_from_left(company_id, item_id, quantity_left):
    return calculate_used_from_left_generic(
        ChemicalItem,
        ChemicalOpeningStock,
        ChemicalTransaction,
        "item_id",
        CHEMICAL_THRESHOLD_KEY,
        company_id,
        item_id,
        quantity_left,
    )


def glue_current_stock(company_id, item_id):
    return get_current_stock_generic(
        GlueItem,
        GlueOpeningStock,
        GlueTransaction,
        "item_id",
        GLUE_THRESHOLD_KEY,
        company_id,
        item_id,
    )


def chemical_current_stock(company_id, item_id):
    return get_current_stock_generic(
        ChemicalItem,
        ChemicalOpeningStock,
        ChemicalTransaction,
        "item_id",
        CHEMICAL_THRESHOLD_KEY,
        company_id,
        item_id,
    )


def glue_dashboard_stats(today):
    return get_dashboard_stats_generic(
        GlueItem,
        GlueOpeningStock,
        GlueTransaction,
        "item_id",
        GLUE_THRESHOLD_KEY,
        today,
    )


def chemical_dashboard_stats(today):
    return get_dashboard_stats_generic(
        ChemicalItem,
        ChemicalOpeningStock,
        ChemicalTransaction,
        "item_id",
        CHEMICAL_THRESHOLD_KEY,
        today,
    )
