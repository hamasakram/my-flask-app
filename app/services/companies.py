from app import db
from app.models import Company

DEFAULT_MATERIALS_COMPANY = "RN Materials"
DEFAULT_INK_COMPANY = "RN Inks"


def get_or_create_default_ink_company() -> Company:
    company = Company.query.filter_by(name=DEFAULT_INK_COMPANY, scope=Company.SCOPE_INK).first()
    if company:
        return company
    company = Company(name=DEFAULT_INK_COMPANY, scope=Company.SCOPE_INK)
    db.session.add(company)
    db.session.flush()
    return company


def get_or_create_default_materials_company() -> Company:
    company = Company.query.filter_by(
        name=DEFAULT_MATERIALS_COMPANY, scope=Company.SCOPE_MATERIALS
    ).first()
    if company:
        return company
    company = Company(name=DEFAULT_MATERIALS_COMPANY, scope=Company.SCOPE_MATERIALS)
    db.session.add(company)
    db.session.flush()
    return company


def get_ink_companies():
    return (
        Company.query.filter_by(is_active=True, scope=Company.SCOPE_INK)
        .order_by(Company.name)
        .all()
    )


def get_material_companies():
    return (
        Company.query.filter_by(is_active=True, scope=Company.SCOPE_MATERIALS)
        .order_by(Company.name)
        .all()
    )


def get_glue_companies():
    return (
        Company.query.filter_by(is_active=True, scope=Company.SCOPE_GLUE)
        .order_by(Company.name)
        .all()
    )


def get_chemical_companies():
    return (
        Company.query.filter_by(is_active=True, scope=Company.SCOPE_CHEMICALS)
        .order_by(Company.name)
        .all()
    )


def get_companies_for_scope(scope: str):
    return (
        Company.query.filter_by(is_active=True, scope=scope)
        .order_by(Company.name)
        .all()
    )
