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
        "weight_per_quantity": "FLOAT",
        "gross_weight": "FLOAT",
        "tw": "FLOAT",
        "net_weight": "FLOAT",
    },
    "glue_transactions": {
        "gross_weight": "FLOAT",
        "tw": "FLOAT",
        "net_weight": "FLOAT",
    },
    "chemical_transactions": {
        "gross_weight": "FLOAT",
        "tw": "FLOAT",
        "net_weight": "FLOAT",
    },
    "sh_gate_passes": {
        "rolls": "FLOAT",
        "gross_weight_per_roll": "FLOAT",
        "net_weight_per_roll": "FLOAT",
    },
    "sh_ledger_entries": {
        "supplier_company_id": "INTEGER",
        "client_company_id": "INTEGER",
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


def _drop_materials_unique_constraint():
    """Remove legacy unique index so duplicate item names are allowed."""
    inspector = inspect(db.engine)
    if not inspector.has_table("materials"):
        return

    dialect = db.engine.dialect.name

    if dialect == "postgresql":
        with db.engine.begin() as conn:
            exists = conn.execute(
                text(
                    """
                    SELECT 1
                    FROM pg_constraint c
                    JOIN pg_class t ON c.conrelid = t.oid
                    WHERE t.relname = 'materials' AND c.conname = 'uq_company_material'
                    """
                )
            ).fetchone()
            if exists:
                conn.execute(text("ALTER TABLE materials DROP CONSTRAINT uq_company_material"))
        return

    if dialect == "sqlite":
        with db.engine.begin() as conn:
            table_sql = conn.execute(
                text(
                    "SELECT sql FROM sqlite_master WHERE type='table' AND name='materials'"
                )
            ).scalar()
            if not table_sql or "uq_company_material" not in table_sql:
                return

            conn.execute(
                text(
                    """
                    CREATE TABLE materials_new (
                        id INTEGER NOT NULL PRIMARY KEY,
                        company_id INTEGER NOT NULL,
                        category VARCHAR(20) NOT NULL DEFAULT 'PET',
                        name VARCHAR(150) NOT NULL,
                        size VARCHAR(100) NOT NULL DEFAULT '',
                        micron VARCHAR(50),
                        low_stock_threshold INTEGER,
                        created_at DATETIME NOT NULL,
                        FOREIGN KEY(company_id) REFERENCES companies (id)
                    )
                    """
                )
            )
            conn.execute(
                text(
                    """
                    INSERT INTO materials_new
                        (id, company_id, category, name, size, micron, low_stock_threshold, created_at)
                    SELECT id, company_id, COALESCE(category, 'PET'), name, size, micron,
                           low_stock_threshold, created_at
                    FROM materials
                    """
                )
            )
            conn.execute(text("DROP TABLE materials"))
            conn.execute(text("ALTER TABLE materials_new RENAME TO materials"))


def ensure_schema():
    for table, columns in COLUMN_MIGRATIONS.items():
        for column, col_type in columns.items():
            _add_column_if_missing(table, column, col_type)

    _drop_materials_unique_constraint()

    inspector = inspect(db.engine)
    if inspector.has_table("companies"):
        columns = {col["name"] for col in inspector.get_columns("companies")}
        if "scope" in columns:
            with db.engine.begin() as conn:
                conn.execute(text("UPDATE companies SET scope = 'ink' WHERE scope IS NULL"))
