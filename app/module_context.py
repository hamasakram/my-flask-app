"""Module registry and navigation helpers."""

from __future__ import annotations

from typing import Optional

from flask import current_app, has_request_context, request, url_for

MODULE_INK = "ink"
MODULE_MATERIALS = "materials"
MODULE_GLUE = "glue"
MODULE_CHEMICALS = "chemicals"
MODULE_SH_TRADERS = "sh_traders"
MODULE_HOME_LEDGER = "home_ledger"
MODULE_BANK_LEDGER = "bank_ledger"
MODULE_JOBS = "jobs"

SESSION_KEY = "stock_module"

ALL_MODULES = (
    MODULE_INK,
    MODULE_MATERIALS,
    MODULE_GLUE,
    MODULE_CHEMICALS,
    MODULE_SH_TRADERS,
    MODULE_HOME_LEDGER,
    MODULE_BANK_LEDGER,
    MODULE_JOBS,
)

MODULE_LABELS = {
    MODULE_INK: "Ink Stock Management",
    MODULE_MATERIALS: "Printing Materials Stock Management",
    MODULE_GLUE: "Glue Management",
    MODULE_CHEMICALS: "Chemicals Management",
    MODULE_SH_TRADERS: "SH Traders",
    MODULE_HOME_LEDGER: "Home Ledger",
    MODULE_BANK_LEDGER: "Bank Ledger",
    MODULE_JOBS: "Job Planning",
}

MODULE_DASHBOARD_ENDPOINTS = {
    MODULE_INK: "main.dashboard",
    MODULE_MATERIALS: "materials_main.dashboard",
    MODULE_GLUE: "glue_main.dashboard",
    MODULE_CHEMICALS: "chemicals_main.dashboard",
    MODULE_SH_TRADERS: "sh_main.dashboard",
    MODULE_HOME_LEDGER: "home_ledger.dashboard",
    MODULE_BANK_LEDGER: "bank_ledger.dashboard",
    MODULE_JOBS: "jobs.dashboard",
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
    if not has_request_context():
        return url_for(endpoint)
    # Dashboard URLs take no arguments and never change while the app runs, so each is built
    # once (per app and script root) instead of 8 url_for() calls on every rendered page.
    cache = current_app.extensions.setdefault("rn_dashboard_urls", {})
    key = (request.script_root, endpoint)
    url = cache.get(key)
    if url is None:
        url = cache[key] = url_for(endpoint)
    return url


def all_module_options(current: Optional[str] = None):
    return [
        {
            "id": module_id,
            "label": module_label(module_id),
            "url": module_dashboard_url(module_id),
            "active": module_id == current,
        }
        for module_id in ALL_MODULES
    ]


_DASHBOARD_ONLY_ALLOWED: frozenset[str | None] = frozenset(MODULE_DASHBOARD_ENDPOINTS.values()) | {
    "auth.login",
    "auth.logout",
    "auth.choose_module",
    "static",
    "ping",
    None,
}


def dashboard_only_allowed_endpoints() -> frozenset[str | None]:
    return _DASHBOARD_ONLY_ALLOWED


def other_module(current: str) -> str:
    try:
        index = ALL_MODULES.index(current)
        return ALL_MODULES[(index + 1) % len(ALL_MODULES)]
    except ValueError:
        return MODULE_INK
