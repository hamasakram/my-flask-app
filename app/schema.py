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


def _make_materials_company_id_nullable():
    """Allow materials stock records without a company."""
    inspector = inspect(db.engine)
    if not inspector.has_table("materials"):
        return

    dialect = db.engine.dialect.name

    if dialect == "postgresql":
        with db.engine.begin() as conn:
            conn.execute(text("ALTER TABLE materials ALTER COLUMN company_id DROP NOT NULL"))
        if inspector.has_table("material_transactions"):
            with db.engine.begin() as conn:
                conn.execute(
                    text("ALTER TABLE material_transactions ALTER COLUMN company_id DROP NOT NULL")
                )
        if inspector.has_table("stock_purchase_receipts"):
            with db.engine.begin() as conn:
                conn.execute(
                    text("ALTER TABLE stock_purchase_receipts ALTER COLUMN company_id DROP NOT NULL")
                )
        return

    if dialect != "sqlite":
        return

    with db.engine.begin() as conn:
        table_sql = conn.execute(
            text("SELECT sql FROM sqlite_master WHERE type='table' AND name='materials'")
        ).scalar()
        if table_sql and "company_id INTEGER NOT NULL" in table_sql:
            conn.execute(
                text(
                    """
                    CREATE TABLE materials_new (
                        id INTEGER NOT NULL PRIMARY KEY,
                        company_id INTEGER,
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

        txn_sql = conn.execute(
            text("SELECT sql FROM sqlite_master WHERE type='table' AND name='material_transactions'")
        ).scalar()
        if txn_sql and "company_id INTEGER NOT NULL" in txn_sql:
            conn.execute(
                text(
                    """
                    CREATE TABLE material_transactions_new (
                        id INTEGER NOT NULL PRIMARY KEY,
                        company_id INTEGER,
                        material_id INTEGER NOT NULL,
                        transaction_type VARCHAR(50) NOT NULL,
                        quantity FLOAT NOT NULL,
                        quantity_left FLOAT,
                        weight_per_quantity FLOAT,
                        gross_weight FLOAT,
                        tw FLOAT,
                        net_weight FLOAT,
                        micron VARCHAR(50),
                        transaction_date DATE NOT NULL,
                        notes TEXT,
                        created_by_id INTEGER,
                        created_at DATETIME NOT NULL,
                        FOREIGN KEY(company_id) REFERENCES companies (id),
                        FOREIGN KEY(material_id) REFERENCES materials (id),
                        FOREIGN KEY(created_by_id) REFERENCES users (id)
                    )
                    """
                )
            )
            conn.execute(
                text(
                    """
                    INSERT INTO material_transactions_new
                        (id, company_id, material_id, transaction_type, quantity, quantity_left,
                         weight_per_quantity, gross_weight, tw, net_weight, micron,
                         transaction_date, notes, created_by_id, created_at)
                    SELECT id, company_id, material_id, transaction_type, quantity, quantity_left,
                           weight_per_quantity, gross_weight, tw, net_weight, micron,
                           transaction_date, notes, created_by_id, created_at
                    FROM material_transactions
                    """
                )
            )
            conn.execute(text("DROP TABLE material_transactions"))
            conn.execute(text("ALTER TABLE material_transactions_new RENAME TO material_transactions"))

        receipt_sql = conn.execute(
            text(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name='stock_purchase_receipts'"
            )
        ).scalar()
        if receipt_sql and "company_id INTEGER NOT NULL" in receipt_sql:
            conn.execute(
                text(
                    """
                    CREATE TABLE stock_purchase_receipts_new (
                        id INTEGER NOT NULL PRIMARY KEY,
                        module VARCHAR(20) NOT NULL,
                        receipt_date DATE NOT NULL,
                        company_id INTEGER,
                        inventory_transaction_id INTEGER,
                        material_transaction_id INTEGER,
                        title VARCHAR(200),
                        amount FLOAT,
                        notes TEXT,
                        screenshot_filename VARCHAR(255),
                        screenshot_data BLOB,
                        screenshot_mimetype VARCHAR(100),
                        created_by_id INTEGER,
                        created_at DATETIME NOT NULL,
                        FOREIGN KEY(company_id) REFERENCES companies (id),
                        FOREIGN KEY(inventory_transaction_id) REFERENCES inventory_transactions (id),
                        FOREIGN KEY(material_transaction_id) REFERENCES material_transactions (id),
                        FOREIGN KEY(created_by_id) REFERENCES users (id)
                    )
                    """
                )
            )
            conn.execute(
                text(
                    """
                    INSERT INTO stock_purchase_receipts_new
                        (id, module, receipt_date, company_id, inventory_transaction_id,
                         material_transaction_id, title, amount, notes, screenshot_filename,
                         screenshot_data, screenshot_mimetype, created_by_id, created_at)
                    SELECT id, module, receipt_date, company_id, inventory_transaction_id,
                           material_transaction_id, title, amount, notes, screenshot_filename,
                           screenshot_data, screenshot_mimetype, created_by_id, created_at
                    FROM stock_purchase_receipts
                    """
                )
            )
            conn.execute(text("DROP TABLE stock_purchase_receipts"))
            conn.execute(text("ALTER TABLE stock_purchase_receipts_new RENAME TO stock_purchase_receipts"))


def _remove_materials_companies():
    """Clear materials opening stock and detach materials from companies."""
    from app.models import AppSetting, Company

    flag_key = "materials_no_companies_v1"
    if AppSetting.query.filter_by(key=flag_key).first():
        return

    _make_materials_company_id_nullable()

    with db.engine.begin() as conn:
        if inspect(db.engine).has_table("material_opening_stock"):
            conn.execute(text("DELETE FROM material_opening_stock"))
        if inspect(db.engine).has_table("material_transactions"):
            conn.execute(text("UPDATE material_transactions SET company_id = NULL"))
        if inspect(db.engine).has_table("materials"):
            conn.execute(text("UPDATE materials SET company_id = NULL"))
        if inspect(db.engine).has_table("stock_purchase_receipts"):
            conn.execute(
                text(
                    "UPDATE stock_purchase_receipts SET company_id = NULL "
                    "WHERE module = 'materials'"
                )
            )

    Company.query.filter_by(scope=Company.SCOPE_MATERIALS).delete(synchronize_session=False)
    db.session.add(AppSetting(key=flag_key, value="done"))
    db.session.commit()


def ensure_schema():
    for table, columns in COLUMN_MIGRATIONS.items():
        for column, col_type in columns.items():
            _add_column_if_missing(table, column, col_type)

    _drop_materials_unique_constraint()
    _migrate_material_opening_stock()
    _remove_materials_companies()
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
