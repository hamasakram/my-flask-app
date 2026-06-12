"""Module registry and navigation helpers."""

from flask import url_for

MODULE_INK = "ink"
MODULE_MATERIALS = "materials"
MODULE_GLUE = "glue"
MODULE_CHEMICALS = "chemicals"

SESSION_KEY = "stock_module"

ALL_MODULES = (MODULE_INK, MODULE_MATERIALS, MODULE_GLUE, MODULE_CHEMICALS)

MODULE_LABELS = {
    MODULE_INK: "Ink Stock Management",
    MODULE_MATERIALS: "Printing Materials Stock Management",
    MODULE_GLUE: "Glue Management",
    MODULE_CHEMICALS: "Chemicals Management",
}

MODULE_DASHBOARD_ENDPOINTS = {
    MODULE_INK: "main.dashboard",
    MODULE_MATERIALS: "materials_main.dashboard",
    MODULE_GLUE: "glue_main.dashboard",
    MODULE_CHEMICALS: "chemicals_main.dashboard",
}

MATERIAL_CATEGORIES = ("PET", "METALIZE", "LD")
INK_UNIT_TYPES = ("Can", "Drum", "Tin")
PRODUCT_UNIT_TYPES = ("Can", "Drum", "Tin", "Kg", "Litre", "Bag", "Box")


def get_active_module():
    from flask import session

    return session.get(SESSION_KEY)


def set_active_module(module: str):
    from flask import session

    session[SESSION_KEY] = module


def clear_active_module():
    from flask import session

    session.pop(SESSION_KEY, None)


def module_label(module: str) -> str:
    return MODULE_LABELS.get(module, "Stock Management")


def module_dashboard_url(module: str) -> str:
    endpoint = MODULE_DASHBOARD_ENDPOINTS.get(module, "main.dashboard")
    return url_for(endpoint)


def all_module_options(current: str | None = None):
    return [
        {
            "id": module_id,
            "label": module_label(module_id),
            "url": module_dashboard_url(module_id),
            "active": module_id == current,
        }
        for module_id in ALL_MODULES
    ]


def other_module(current: str) -> str:
    try:
        index = ALL_MODULES.index(current)
        return ALL_MODULES[(index + 1) % len(ALL_MODULES)]
    except ValueError:
        return MODULE_INK
