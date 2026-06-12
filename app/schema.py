from sqlalchemy import inspect, text

from app import db

COLUMN_MIGRATIONS = {
    "ink_types": {
        "color_code": "VARCHAR(50)",
        "unit_type": "VARCHAR(20)",
    },
    "materials": {
        "category": "VARCHAR(20)",
        "micron": "VARCHAR(50)",
    },
    "material_transactions": {
        "weight_per_quantity": "FLOAT",
        "gross_weight": "FLOAT",
        "tw": "FLOAT",
        "net_weight": "FLOAT",
        "micron": "VARCHAR(50)",
    },
    "companies": {
        "scope": "VARCHAR(20) DEFAULT 'ink'",
    },
    "inventory_transactions": {
        "quantity_left": "FLOAT",
    },
}


def _add_column_if_missing(table: str, column: str, col_type: str):
    inspector = inspect(db.engine)
    if not inspector.has_table(table):
        return

    columns = {col["name"] for col in inspector.get_columns(table)}
    if column not in columns:
        with db.engine.begin() as conn:
            conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}"))


def ensure_schema():
    for table, columns in COLUMN_MIGRATIONS.items():
        for column, col_type in columns.items():
            _add_column_if_missing(table, column, col_type)

    inspector = inspect(db.engine)
    if inspector.has_table("companies"):
        columns = {col["name"] for col in inspector.get_columns("companies")}
        if "scope" in columns:
            with db.engine.begin() as conn:
                conn.execute(text("UPDATE companies SET scope = 'ink' WHERE scope IS NULL"))
