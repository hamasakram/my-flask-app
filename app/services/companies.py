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
