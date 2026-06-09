from flask import session, url_for

MODULE_INK = "ink"
MODULE_MATERIALS = "materials"
SESSION_KEY = "stock_module"

MODULE_LABELS = {
    MODULE_INK: "Ink Stock Management",
    MODULE_MATERIALS: "Printing Materials Stock Management",
}


def get_active_module():
    return session.get(SESSION_KEY)


def set_active_module(module: str):
    session[SESSION_KEY] = module


def clear_active_module():
    session.pop(SESSION_KEY, None)


def module_label(module: str) -> str:
    return MODULE_LABELS.get(module, "Stock Management")


def module_dashboard_endpoint(module: str) -> str:
    if module == MODULE_MATERIALS:
        return "materials_main.dashboard"
    return "main.dashboard"


def module_dashboard_url(module: str) -> str:
    return url_for(module_dashboard_endpoint(module))


def other_module(current: str) -> str:
    return MODULE_MATERIALS if current == MODULE_INK else MODULE_INK
