from sqlalchemy import inspect, text

from app import db


def ensure_schema():
    inspector = inspect(db.engine)

    if inspector.has_table("inventory_transactions"):
        columns = {col["name"] for col in inspector.get_columns("inventory_transactions")}
        if "quantity_left" not in columns:
            with db.engine.begin() as conn:
                conn.execute(
                    text("ALTER TABLE inventory_transactions ADD COLUMN quantity_left FLOAT")
                )

    if inspector.has_table("companies"):
        columns = {col["name"] for col in inspector.get_columns("companies")}
        if "scope" not in columns:
            with db.engine.begin() as conn:
                conn.execute(
                    text("ALTER TABLE companies ADD COLUMN scope VARCHAR(20) DEFAULT 'ink'")
                )
                conn.execute(text("UPDATE companies SET scope = 'ink' WHERE scope IS NULL"))
