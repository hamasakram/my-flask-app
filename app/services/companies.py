from app.models import Company


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
