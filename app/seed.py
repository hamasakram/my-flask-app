from app import db
from app.models import AppSetting, Company, User


COMPANY_NAMES = [
    "RL Inks",
    "IPIC",
    "Hi Tech",
    "DIC",
    "DIC HR",
    "RL SHR",
]


def seed_database():
    if not AppSetting.query.filter_by(key="default_low_stock_threshold").first():
        db.session.add(
            AppSetting(key="default_low_stock_threshold", value="50")
        )

    for name in COMPANY_NAMES:
        if not Company.query.filter_by(name=name).first():
            db.session.add(Company(name=name))

    if not User.query.filter_by(username="admin").first():
        admin = User(username="admin", role=User.ROLE_ADMIN)
        admin.set_password("admin123")
        db.session.add(admin)

    if not User.query.filter_by(username="manager").first():
        manager = User(username="manager", role=User.ROLE_MANAGER)
        manager.set_password("manager123")
        db.session.add(manager)

    if not User.query.filter_by(username="viewer").first():
        viewer = User(username="viewer", role=User.ROLE_VIEWER)
        viewer.set_password("viewer123")
        db.session.add(viewer)

    db.session.commit()
