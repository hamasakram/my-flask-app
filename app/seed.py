from app import db
from app.models import AppSetting, Company, MaterialOpeningStock, OpeningStock, User


COMPANY_NAMES = [
    "RL Inks",
    "IPIC",
    "Hi Tech",
    "DIC",
    "DIC HR",
    "RL SHR",
]


def clear_all_opening_stock():
    """Remove all ink and material opening stock records."""
    OpeningStock.query.delete()
    MaterialOpeningStock.query.delete()


def _clear_opening_stock_once():
    flag_key = "opening_stock_reset_june2025"
    if AppSetting.query.filter_by(key=flag_key).first():
        return

    clear_all_opening_stock()
    db.session.add(AppSetting(key=flag_key, value="done"))


def seed_database():
    if not AppSetting.query.filter_by(key="default_low_stock_threshold").first():
        db.session.add(
            AppSetting(key="default_low_stock_threshold", value="50")
        )

    if not AppSetting.query.filter_by(key="default_material_low_stock_threshold").first():
        db.session.add(
            AppSetting(key="default_material_low_stock_threshold", value="50")
        )

    if not AppSetting.query.filter_by(key="default_glue_low_stock_threshold").first():
        db.session.add(AppSetting(key="default_glue_low_stock_threshold", value="50"))

    if not AppSetting.query.filter_by(key="default_chemical_low_stock_threshold").first():
        db.session.add(AppSetting(key="default_chemical_low_stock_threshold", value="50"))

    for name in COMPANY_NAMES:
        if not Company.query.filter_by(name=name).first():
            db.session.add(Company(name=name, scope=Company.SCOPE_INK))

    legacy_admin = User.query.filter_by(username="admin").first()
    if legacy_admin:
        legacy_admin.is_active = False

    admin = User.query.filter_by(username="hamas9478").first()
    if not admin:
        admin = User(username="hamas9478", role=User.ROLE_ADMIN)
        db.session.add(admin)
    admin.set_password("hamas9478")
    admin.is_active = True
    admin.role = User.ROLE_ADMIN

    if not User.query.filter_by(username="manager").first():
        manager = User(username="manager", role=User.ROLE_MANAGER)
        manager.set_password("manager123")
        db.session.add(manager)

    if not User.query.filter_by(username="viewer").first():
        viewer = User(username="viewer", role=User.ROLE_VIEWER)
        viewer.set_password("viewer123")
        db.session.add(viewer)

    _clear_opening_stock_once()
    db.session.commit()
