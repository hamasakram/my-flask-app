from sqlalchemy import inspect, text

from app import db


def ensure_schema():
    inspector = inspect(db.engine)
    if not inspector.has_table("inventory_transactions"):
        return

    columns = {col["name"] for col in inspector.get_columns("inventory_transactions")}
    if "quantity_left" not in columns:
        with db.engine.begin() as conn:
            conn.execute(
                text("ALTER TABLE inventory_transactions ADD COLUMN quantity_left FLOAT")
            )
