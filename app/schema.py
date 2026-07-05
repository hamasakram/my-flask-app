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
    "material_opening_stock": {
        "material_name": "VARCHAR(255)",
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
        "cone_weight_per_roll": "FLOAT",
    },
    "sh_purchases": {
        "client_rate_per_kg": "FLOAT",
        "client_total_amount": "FLOAT",
        "has_partnership": "BOOLEAN DEFAULT FALSE",
    },
    "sh_ledger_entries": {
        "supplier_company_id": "INTEGER",
        "client_company_id": "INTEGER",
        "partner_company_id": "INTEGER",
        "purchase_id": "INTEGER",
    },
    "bank_ledger_entries": {
        "entry_type": "VARCHAR(20) DEFAULT 'standard'",
        "transfer_id": "INTEGER",
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


def _migrate_material_opening_stock():
    """Move material opening stock to manual material names without company."""
    inspector = inspect(db.engine)
    table = "material_opening_stock"
    if not inspector.has_table(table):
        return

    columns = {col["name"] for col in inspector.get_columns(table)}
    if "material_name" not in columns:
        _add_column_if_missing(table, "material_name", "VARCHAR(255)")
        columns.add("material_name")

    if "material_id" in columns:
        from app.models import Material

        rows = db.session.execute(
            text(
                """
                SELECT id, material_id
                FROM material_opening_stock
                WHERE material_name IS NULL OR TRIM(material_name) = ''
                """
            )
        ).fetchall()
        for row_id, material_id in rows:
            material = Material.query.get(material_id)
            if material:
                db.session.execute(
                    text(
                        "UPDATE material_opening_stock SET material_name = :name WHERE id = :id"
                    ),
                    {"name": material.display_name, "id": row_id},
                )
        db.session.commit()

    if "material_name" in columns:
        with db.engine.begin() as conn:
            conn.execute(
                text(
                    """
                    UPDATE material_opening_stock
                    SET material_name = 'Unknown'
                    WHERE material_name IS NULL OR TRIM(material_name) = ''
                    """
                )
            )

    dialect = db.engine.dialect.name
    if dialect == "postgresql":
        with db.engine.begin() as conn:
            exists = conn.execute(
                text(
                    """
                    SELECT 1
                    FROM pg_constraint c
                    JOIN pg_class t ON c.conrelid = t.oid
                    WHERE t.relname = 'material_opening_stock'
                      AND c.conname = 'uq_material_opening_stock'
                    """
                )
            ).fetchone()
            if exists:
                conn.execute(
                    text(
                        "ALTER TABLE material_opening_stock "
                        "DROP CONSTRAINT uq_material_opening_stock"
                    )
                )

            columns = {col["name"] for col in inspector.get_columns(table)}
            if "company_id" in columns:
                conn.execute(
                    text("ALTER TABLE material_opening_stock DROP COLUMN company_id")
                )
            if "material_id" in columns:
                conn.execute(
                    text("ALTER TABLE material_opening_stock DROP COLUMN material_id")
                )

            name_unique = conn.execute(
                text(
                    """
                    SELECT 1
                    FROM pg_constraint c
                    JOIN pg_class t ON c.conrelid = t.oid
                    WHERE t.relname = 'material_opening_stock'
                      AND c.conname = 'uq_material_opening_stock_name'
                    """
                )
            ).fetchone()
            if not name_unique:
                conn.execute(
                    text(
                        "ALTER TABLE material_opening_stock "
                        "ADD CONSTRAINT uq_material_opening_stock_name "
                        "UNIQUE (material_name)"
                    )
                )


def _remove_auto_synced_purchase_ledger_entries():
    from app.services.sh_traders import remove_auto_synced_purchase_ledger_entries

    remove_auto_synced_purchase_ledger_entries()


def ensure_schema():
    for table, columns in COLUMN_MIGRATIONS.items():
        for column, col_type in columns.items():
            _add_column_if_missing(table, column, col_type)

    _drop_materials_unique_constraint()
    _migrate_material_opening_stock()
    _remove_auto_synced_purchase_ledger_entries()

    blob_type = "BYTEA" if db.engine.dialect.name == "postgresql" else "BLOB"
    _add_column_if_missing("sh_payment_screenshots", "screenshot_data", blob_type)
    _add_column_if_missing("sh_payment_screenshots", "screenshot_mimetype", "VARCHAR(100)")
    _add_column_if_missing("sh_gate_pass_screenshots", "screenshot_data", blob_type)
    _add_column_if_missing("sh_gate_pass_screenshots", "screenshot_mimetype", "VARCHAR(100)")
    _add_column_if_missing("stock_purchase_receipts", "screenshot_data", blob_type)
    _add_column_if_missing("stock_purchase_receipts", "screenshot_mimetype", "VARCHAR(100)")

    inspector = inspect(db.engine)
    if inspector.has_table("companies"):
        columns = {col["name"] for col in inspector.get_columns("companies")}
        if "scope" in columns:
            with db.engine.begin() as conn:
                conn.execute(text("UPDATE companies SET scope = 'ink' WHERE scope IS NULL"))
