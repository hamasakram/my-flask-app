from datetime import datetime, timezone

from flask_login import UserMixin
from werkzeug.security import check_password_hash, generate_password_hash

from app import db


def utcnow():
    return datetime.now(timezone.utc)


class Company(db.Model):
    __tablename__ = "companies"

    SCOPE_INK = "ink"
    SCOPE_MATERIALS = "materials"
    SCOPE_GLUE = "glue"
    SCOPE_CHEMICALS = "chemicals"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), unique=True, nullable=False)
    scope = db.Column(db.String(20), nullable=False, default=SCOPE_INK)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)

    ink_types = db.relationship("InkType", back_populates="company", lazy="dynamic")
    opening_stocks = db.relationship("OpeningStock", back_populates="company", lazy="dynamic")
    transactions = db.relationship("InventoryTransaction", back_populates="company", lazy="dynamic")
    materials = db.relationship("Material", back_populates="company", lazy="dynamic")
    material_opening_stocks = db.relationship(
        "MaterialOpeningStock", back_populates="company", lazy="dynamic"
    )
    material_transactions = db.relationship(
        "MaterialTransaction", back_populates="company", lazy="dynamic"
    )
    glue_items = db.relationship("GlueItem", back_populates="company", lazy="dynamic")
    glue_opening_stocks = db.relationship(
        "GlueOpeningStock", back_populates="company", lazy="dynamic"
    )
    glue_transactions = db.relationship(
        "GlueTransaction", back_populates="company", lazy="dynamic"
    )
    chemical_items = db.relationship("ChemicalItem", back_populates="company", lazy="dynamic")
    chemical_opening_stocks = db.relationship(
        "ChemicalOpeningStock", back_populates="company", lazy="dynamic"
    )
    chemical_transactions = db.relationship(
        "ChemicalTransaction", back_populates="company", lazy="dynamic"
    )


class InkType(db.Model):
    __tablename__ = "ink_types"

    id = db.Column(db.Integer, primary_key=True)
    company_id = db.Column(db.Integer, db.ForeignKey("companies.id"), nullable=False)
    name = db.Column(db.String(150), nullable=False)
    color_code = db.Column(db.String(50))
    unit_type = db.Column(db.String(20))
    low_stock_threshold = db.Column(db.Integer, nullable=True)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)

    company = db.relationship("Company", back_populates="ink_types")
    opening_stocks = db.relationship("OpeningStock", back_populates="ink_type", lazy="dynamic")
    transactions = db.relationship("InventoryTransaction", back_populates="ink_type", lazy="dynamic")

    __table_args__ = (db.UniqueConstraint("company_id", "name", name="uq_company_ink"),)


class OpeningStock(db.Model):
    __tablename__ = "opening_stock"

    id = db.Column(db.Integer, primary_key=True)
    company_id = db.Column(db.Integer, db.ForeignKey("companies.id"), nullable=False)
    ink_type_id = db.Column(db.Integer, db.ForeignKey("ink_types.id"), nullable=False)
    quantity = db.Column(db.Float, nullable=False, default=0)
    as_of_date = db.Column(db.Date, nullable=False)
    notes = db.Column(db.Text)
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow, nullable=False)

    company = db.relationship("Company", back_populates="opening_stocks")
    ink_type = db.relationship("InkType", back_populates="opening_stocks")
    created_by = db.relationship("User", foreign_keys=[created_by_id])

    __table_args__ = (db.UniqueConstraint("company_id", "ink_type_id", name="uq_opening_stock"),)


class InventoryTransaction(db.Model):
    __tablename__ = "inventory_transactions"

    TRANSACTION_RECEIVED = "Stock Received"
    TRANSACTION_ISSUED = "Issued to Use"
    TRANSACTION_USED = "Stock Used"
    TRANSACTION_TYPES = (TRANSACTION_RECEIVED, TRANSACTION_ISSUED, TRANSACTION_USED)

    id = db.Column(db.Integer, primary_key=True)
    company_id = db.Column(db.Integer, db.ForeignKey("companies.id"), nullable=False)
    ink_type_id = db.Column(db.Integer, db.ForeignKey("ink_types.id"), nullable=False)
    transaction_type = db.Column(db.String(50), nullable=False)
    quantity = db.Column(db.Float, nullable=False)
    quantity_left = db.Column(db.Float, nullable=True)
    weight_per_quantity = db.Column(db.Float, nullable=True)
    gross_weight = db.Column(db.Float, nullable=True)
    tw = db.Column(db.Float, nullable=True)
    net_weight = db.Column(db.Float, nullable=True)
    transaction_date = db.Column(db.Date, nullable=False)
    notes = db.Column(db.Text)
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)

    company = db.relationship("Company", back_populates="transactions")
    ink_type = db.relationship("InkType", back_populates="transactions")
    created_by = db.relationship("User", foreign_keys=[created_by_id])


class Material(db.Model):
    __tablename__ = "materials"

    id = db.Column(db.Integer, primary_key=True)
    company_id = db.Column(db.Integer, db.ForeignKey("companies.id"), nullable=False)
    category = db.Column(db.String(20), nullable=False, default="PET")
    name = db.Column(db.String(150), nullable=False)
    size = db.Column(db.String(100), nullable=False, default="")
    micron = db.Column(db.String(50))
    low_stock_threshold = db.Column(db.Integer, nullable=True)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)

    company = db.relationship("Company", back_populates="materials")
    opening_stocks = db.relationship(
        "MaterialOpeningStock", back_populates="material", lazy="dynamic"
    )
    transactions = db.relationship(
        "MaterialTransaction", back_populates="material", lazy="dynamic"
    )

    @property
    def display_name(self):
        parts = [self.category, self.name]
        if self.size:
            parts.append(self.size)
        if self.micron:
            parts.append(f"{self.micron}μ")
        return " · ".join(parts)


class MaterialOpeningStock(db.Model):
    __tablename__ = "material_opening_stock"

    id = db.Column(db.Integer, primary_key=True)
    company_id = db.Column(db.Integer, db.ForeignKey("companies.id"), nullable=False)
    material_id = db.Column(db.Integer, db.ForeignKey("materials.id"), nullable=False)
    quantity = db.Column(db.Float, nullable=False, default=0)
    as_of_date = db.Column(db.Date, nullable=False)
    notes = db.Column(db.Text)
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow, nullable=False)

    company = db.relationship("Company", back_populates="material_opening_stocks")
    material = db.relationship("Material", back_populates="opening_stocks")
    created_by = db.relationship("User", foreign_keys=[created_by_id])

    __table_args__ = (
        db.UniqueConstraint("company_id", "material_id", name="uq_material_opening_stock"),
    )


class MaterialTransaction(db.Model):
    __tablename__ = "material_transactions"

    TRANSACTION_RECEIVED = "Stock Received"
    TRANSACTION_USED = "Stock Used"
    TRANSACTION_TYPES = (TRANSACTION_RECEIVED, TRANSACTION_USED)

    id = db.Column(db.Integer, primary_key=True)
    company_id = db.Column(db.Integer, db.ForeignKey("companies.id"), nullable=False)
    material_id = db.Column(db.Integer, db.ForeignKey("materials.id"), nullable=False)
    transaction_type = db.Column(db.String(50), nullable=False)
    quantity = db.Column(db.Float, nullable=False)
    quantity_left = db.Column(db.Float, nullable=True)
    weight_per_quantity = db.Column(db.Float, nullable=True)
    gross_weight = db.Column(db.Float, nullable=True)
    tw = db.Column(db.Float, nullable=True)
    net_weight = db.Column(db.Float, nullable=True)
    micron = db.Column(db.String(50))
    transaction_date = db.Column(db.Date, nullable=False)
    notes = db.Column(db.Text)
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)

    company = db.relationship("Company", back_populates="material_transactions")
    material = db.relationship("Material", back_populates="transactions")
    created_by = db.relationship("User", foreign_keys=[created_by_id])


class StockPurchaseReceipt(db.Model):
    """Purchase receipt screenshot for ink or materials stock received."""

    __tablename__ = "stock_purchase_receipts"

    MODULE_INK = "ink"
    MODULE_MATERIALS = "materials"

    id = db.Column(db.Integer, primary_key=True)
    module = db.Column(db.String(20), nullable=False)
    receipt_date = db.Column(db.Date, nullable=False)
    company_id = db.Column(db.Integer, db.ForeignKey("companies.id"), nullable=False)
    inventory_transaction_id = db.Column(
        db.Integer, db.ForeignKey("inventory_transactions.id"), nullable=True
    )
    material_transaction_id = db.Column(
        db.Integer, db.ForeignKey("material_transactions.id"), nullable=True
    )
    title = db.Column(db.String(200))
    amount = db.Column(db.Float)
    screenshot_filename = db.Column(db.String(255), nullable=False)
    screenshot_data = db.Column(db.LargeBinary)
    screenshot_mimetype = db.Column(db.String(100))
    notes = db.Column(db.Text)
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)

    company = db.relationship("Company")
    inventory_transaction = db.relationship("InventoryTransaction")
    material_transaction = db.relationship("MaterialTransaction")
    created_by = db.relationship("User", foreign_keys=[created_by_id])


class GlueItem(db.Model):
    __tablename__ = "glue_items"

    id = db.Column(db.Integer, primary_key=True)
    company_id = db.Column(db.Integer, db.ForeignKey("companies.id"), nullable=False)
    name = db.Column(db.String(150), nullable=False)
    unit_type = db.Column(db.String(20), nullable=False, default="Kg")
    low_stock_threshold = db.Column(db.Integer, nullable=True)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)

    company = db.relationship("Company", back_populates="glue_items")
    opening_stocks = db.relationship("GlueOpeningStock", back_populates="item", lazy="dynamic")
    transactions = db.relationship("GlueTransaction", back_populates="item", lazy="dynamic")

    __table_args__ = (db.UniqueConstraint("company_id", "name", name="uq_company_glue"),)

    @property
    def display_name(self):
        return f"{self.name} ({self.unit_type})"


class GlueOpeningStock(db.Model):
    __tablename__ = "glue_opening_stock"

    id = db.Column(db.Integer, primary_key=True)
    company_id = db.Column(db.Integer, db.ForeignKey("companies.id"), nullable=False)
    item_id = db.Column(db.Integer, db.ForeignKey("glue_items.id"), nullable=False)
    quantity = db.Column(db.Float, nullable=False, default=0)
    as_of_date = db.Column(db.Date, nullable=False)
    notes = db.Column(db.Text)
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)

    company = db.relationship("Company", back_populates="glue_opening_stocks")
    item = db.relationship("GlueItem", back_populates="opening_stocks")
    created_by = db.relationship("User", foreign_keys=[created_by_id])

    __table_args__ = (db.UniqueConstraint("company_id", "item_id", name="uq_glue_opening_stock"),)


class GlueTransaction(db.Model):
    __tablename__ = "glue_transactions"

    TRANSACTION_RECEIVED = "Stock Received"
    TRANSACTION_USED = "Stock Used"

    id = db.Column(db.Integer, primary_key=True)
    company_id = db.Column(db.Integer, db.ForeignKey("companies.id"), nullable=False)
    item_id = db.Column(db.Integer, db.ForeignKey("glue_items.id"), nullable=False)
    transaction_type = db.Column(db.String(50), nullable=False)
    quantity = db.Column(db.Float, nullable=False)
    quantity_left = db.Column(db.Float, nullable=True)
    weight_per_quantity = db.Column(db.Float, nullable=True)
    gross_weight = db.Column(db.Float, nullable=True)
    tw = db.Column(db.Float, nullable=True)
    net_weight = db.Column(db.Float, nullable=True)
    transaction_date = db.Column(db.Date, nullable=False)
    notes = db.Column(db.Text)
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)

    company = db.relationship("Company", back_populates="glue_transactions")
    item = db.relationship("GlueItem", back_populates="transactions")
    created_by = db.relationship("User", foreign_keys=[created_by_id])


class ChemicalItem(db.Model):
    __tablename__ = "chemical_items"

    id = db.Column(db.Integer, primary_key=True)
    company_id = db.Column(db.Integer, db.ForeignKey("companies.id"), nullable=False)
    name = db.Column(db.String(150), nullable=False)
    unit_type = db.Column(db.String(20), nullable=False, default="Kg")
    low_stock_threshold = db.Column(db.Integer, nullable=True)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)

    company = db.relationship("Company", back_populates="chemical_items")
    opening_stocks = db.relationship("ChemicalOpeningStock", back_populates="item", lazy="dynamic")
    transactions = db.relationship("ChemicalTransaction", back_populates="item", lazy="dynamic")

    __table_args__ = (db.UniqueConstraint("company_id", "name", name="uq_company_chemical"),)

    @property
    def display_name(self):
        return f"{self.name} ({self.unit_type})"


class ChemicalOpeningStock(db.Model):
    __tablename__ = "chemical_opening_stock"

    id = db.Column(db.Integer, primary_key=True)
    company_id = db.Column(db.Integer, db.ForeignKey("companies.id"), nullable=False)
    item_id = db.Column(db.Integer, db.ForeignKey("chemical_items.id"), nullable=False)
    quantity = db.Column(db.Float, nullable=False, default=0)
    as_of_date = db.Column(db.Date, nullable=False)
    notes = db.Column(db.Text)
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)

    company = db.relationship("Company", back_populates="chemical_opening_stocks")
    item = db.relationship("ChemicalItem", back_populates="opening_stocks")
    created_by = db.relationship("User", foreign_keys=[created_by_id])

    __table_args__ = (
        db.UniqueConstraint("company_id", "item_id", name="uq_chemical_opening_stock"),
    )


class ChemicalTransaction(db.Model):
    __tablename__ = "chemical_transactions"

    TRANSACTION_RECEIVED = "Stock Received"
    TRANSACTION_USED = "Stock Used"

    id = db.Column(db.Integer, primary_key=True)
    company_id = db.Column(db.Integer, db.ForeignKey("companies.id"), nullable=False)
    item_id = db.Column(db.Integer, db.ForeignKey("chemical_items.id"), nullable=False)
    transaction_type = db.Column(db.String(50), nullable=False)
    quantity = db.Column(db.Float, nullable=False)
    quantity_left = db.Column(db.Float, nullable=True)
    weight_per_quantity = db.Column(db.Float, nullable=True)
    gross_weight = db.Column(db.Float, nullable=True)
    tw = db.Column(db.Float, nullable=True)
    net_weight = db.Column(db.Float, nullable=True)
    transaction_date = db.Column(db.Date, nullable=False)
    notes = db.Column(db.Text)
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)

    company = db.relationship("Company", back_populates="chemical_transactions")
    item = db.relationship("ChemicalItem", back_populates="transactions")
    created_by = db.relationship("User", foreign_keys=[created_by_id])


class User(UserMixin, db.Model):
    __tablename__ = "users"

    ROLE_ADMIN = "admin"
    ROLE_MANAGER = "manager"
    ROLE_VIEWER = "viewer"
    ROLE_DASHBOARD = "dashboard"
    ROLES = (ROLE_ADMIN, ROLE_MANAGER, ROLE_VIEWER, ROLE_DASHBOARD)

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    role = db.Column(db.String(20), nullable=False, default=ROLE_VIEWER)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    def can_edit(self):
        return self.role in (self.ROLE_ADMIN, self.ROLE_MANAGER)

    def is_dashboard_only(self):
        return self.role == self.ROLE_DASHBOARD

    def can_view_sensitive(self):
        return not self.is_dashboard_only()

    def is_admin(self):
        return self.role == self.ROLE_ADMIN


class AuditLog(db.Model):
    __tablename__ = "audit_logs"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    action = db.Column(db.String(50), nullable=False)
    entity_type = db.Column(db.String(50), nullable=False)
    entity_id = db.Column(db.Integer)
    details = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)

    user = db.relationship("User", foreign_keys=[user_id])


class AppSetting(db.Model):
    __tablename__ = "app_settings"

    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(100), unique=True, nullable=False)
    value = db.Column(db.String(255), nullable=False)


class ShSupplierCompany(db.Model):
    """Supplier companies SH Traders purchases material from."""

    __tablename__ = "sh_supplier_companies"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(150), unique=True, nullable=False)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)

    purchases = db.relationship("ShPurchase", back_populates="supplier", lazy="dynamic")


class ShClientCompany(db.Model):
    """Client companies — purchased on behalf of (Purchased For)."""

    __tablename__ = "sh_client_companies"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(150), unique=True, nullable=False)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)

    purchases = db.relationship("ShPurchase", back_populates="client", lazy="dynamic")


class ShPurchase(db.Model):
    __tablename__ = "sh_purchases"

    id = db.Column(db.Integer, primary_key=True)
    date_purchased = db.Column(db.Date, nullable=False)
    supplier_company_id = db.Column(
        db.Integer, db.ForeignKey("sh_supplier_companies.id"), nullable=False
    )
    material_name = db.Column(db.String(150), nullable=False)
    size = db.Column(db.String(100), default="")
    micron = db.Column(db.String(50))
    total_kg = db.Column(db.Float, nullable=False)
    rate_per_1000_kg = db.Column(db.Float, nullable=False)
    total_amount = db.Column(db.Float, nullable=False)
    paid_amount = db.Column(db.Float, nullable=False, default=0)
    client_rate_per_kg = db.Column(db.Float, nullable=True)
    client_total_amount = db.Column(db.Float, nullable=True)
    client_company_id = db.Column(
        db.Integer, db.ForeignKey("sh_client_companies.id"), nullable=False
    )
    notes = db.Column(db.Text)
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)

    supplier = db.relationship("ShSupplierCompany", back_populates="purchases")
    client = db.relationship("ShClientCompany", back_populates="purchases")
    created_by = db.relationship("User", foreign_keys=[created_by_id])

    @property
    def amount_due(self) -> float:
        return float(self.total_amount or 0) - float(self.paid_amount or 0)


class ShOpeningBalance(db.Model):
    __tablename__ = "sh_opening_balance"

    id = db.Column(db.Integer, primary_key=True)
    amount = db.Column(db.Float, nullable=False, default=0)
    notes = db.Column(db.Text)
    set_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow, nullable=False)

    set_by = db.relationship("User", foreign_keys=[set_by_id])


class ShLedgerEntry(db.Model):
    __tablename__ = "sh_ledger_entries"

    id = db.Column(db.Integer, primary_key=True)
    entry_date = db.Column(db.Date, nullable=False)
    debit = db.Column(db.Float, nullable=False, default=0)
    credit = db.Column(db.Float, nullable=False, default=0)
    supplier_company_id = db.Column(
        db.Integer, db.ForeignKey("sh_supplier_companies.id"), nullable=True
    )
    client_company_id = db.Column(
        db.Integer, db.ForeignKey("sh_client_companies.id"), nullable=True
    )
    notes = db.Column(db.Text)
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)

    supplier = db.relationship("ShSupplierCompany")
    client = db.relationship("ShClientCompany")
    created_by = db.relationship("User", foreign_keys=[created_by_id])


class ShPaymentScreenshot(db.Model):
    """Payment proof screenshot uploaded for supplier payments."""

    __tablename__ = "sh_payment_screenshots"

    id = db.Column(db.Integer, primary_key=True)
    payment_date = db.Column(db.Date, nullable=False)
    supplier_company_id = db.Column(
        db.Integer, db.ForeignKey("sh_supplier_companies.id"), nullable=False
    )
    amount_paid = db.Column(db.Float)
    purchase_id = db.Column(db.Integer, db.ForeignKey("sh_purchases.id"))
    screenshot_filename = db.Column(db.String(255), nullable=False)
    screenshot_data = db.Column(db.LargeBinary)
    screenshot_mimetype = db.Column(db.String(100))
    notes = db.Column(db.Text)
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)

    supplier = db.relationship("ShSupplierCompany")
    purchase = db.relationship("ShPurchase")
    created_by = db.relationship("User", foreign_keys=[created_by_id])


class ShGatePass(db.Model):
    __tablename__ = "sh_gate_passes"

    id = db.Column(db.Integer, primary_key=True)
    gate_pass_number = db.Column(db.String(30), unique=True, nullable=False)
    issued_at = db.Column(db.DateTime, default=utcnow, nullable=False)
    sold_to_client_id = db.Column(
        db.Integer, db.ForeignKey("sh_client_companies.id"), nullable=False
    )
    supplier_company_id = db.Column(
        db.Integer, db.ForeignKey("sh_supplier_companies.id"), nullable=False
    )
    purchase_id = db.Column(db.Integer, db.ForeignKey("sh_purchases.id"))
    material_name = db.Column(db.String(150), nullable=False)
    size = db.Column(db.String(100), default="")
    micron = db.Column(db.String(50))
    rolls = db.Column(db.Float)
    gross_weight_per_roll = db.Column(db.Float)
    net_weight_per_roll = db.Column(db.Float)
    gross_weight = db.Column(db.Float, nullable=False)
    net_weight = db.Column(db.Float, nullable=False)
    amount_per_kg = db.Column(db.Float, nullable=False)
    total_amount = db.Column(db.Float, nullable=False)
    notes = db.Column(db.Text)
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)

    sold_to = db.relationship("ShClientCompany")
    supplier = db.relationship("ShSupplierCompany")
    purchase = db.relationship("ShPurchase")
    created_by = db.relationship("User", foreign_keys=[created_by_id])


class HomeParty(db.Model):
    """Party in Home Ledger — person or entity for payments to give or receive."""

    __tablename__ = "home_parties"

    KIND_TO_PAY = "to_pay"
    KIND_TO_RECEIVE = "to_receive"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(150), unique=True, nullable=False)
    balance_kind = db.Column(db.String(20), nullable=False, default=KIND_TO_PAY)
    opening_amount = db.Column(db.Float, nullable=False, default=0)
    notes = db.Column(db.Text)
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)

    entries = db.relationship(
        "HomeLedgerEntry", back_populates="party", lazy="dynamic", cascade="all, delete-orphan"
    )
    created_by = db.relationship("User", foreign_keys=[created_by_id])

    @property
    def kind_label(self) -> str:
        if self.balance_kind == self.KIND_TO_RECEIVE:
            return "To Receive"
        return "To Pay"


class HomeLedgerEntry(db.Model):
    __tablename__ = "home_ledger_entries"

    id = db.Column(db.Integer, primary_key=True)
    party_id = db.Column(db.Integer, db.ForeignKey("home_parties.id"), nullable=False)
    entry_date = db.Column(db.Date, nullable=False)
    given = db.Column(db.Float, nullable=False, default=0)
    received = db.Column(db.Float, nullable=False, default=0)
    notes = db.Column(db.Text)
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)

    party = db.relationship("HomeParty", back_populates="entries")
    created_by = db.relationship("User", foreign_keys=[created_by_id])


class BankAccount(db.Model):
    """Bank account in Bank Ledger — each account gets its own ledger page."""

    __tablename__ = "bank_accounts"

    id = db.Column(db.Integer, primary_key=True)
    bank_name = db.Column(db.String(150), nullable=False)
    account_title = db.Column(db.String(150))
    account_number = db.Column(db.String(50))
    branch = db.Column(db.String(150))
    opening_balance = db.Column(db.Float, nullable=False, default=0)
    notes = db.Column(db.Text)
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)

    entries = db.relationship(
        "BankLedgerEntry", back_populates="bank", lazy="dynamic", cascade="all, delete-orphan"
    )
    created_by = db.relationship("User", foreign_keys=[created_by_id])

    @property
    def display_name(self) -> str:
        if self.account_number:
            return f"{self.bank_name} — {self.account_number}"
        return self.bank_name


class BankLedgerEntry(db.Model):
    __tablename__ = "bank_ledger_entries"

    TYPE_STANDARD = "standard"
    TYPE_TRANSFER_IN = "transfer_in"
    TYPE_TRANSFER_OUT = "transfer_out"

    id = db.Column(db.Integer, primary_key=True)
    bank_id = db.Column(db.Integer, db.ForeignKey("bank_accounts.id"), nullable=False)
    entry_date = db.Column(db.Date, nullable=False)
    deposit = db.Column(db.Float, nullable=False, default=0)
    withdrawal = db.Column(db.Float, nullable=False, default=0)
    entry_type = db.Column(db.String(20), nullable=False, default=TYPE_STANDARD)
    transfer_id = db.Column(db.Integer, db.ForeignKey("bank_transfers.id", ondelete="CASCADE"))
    notes = db.Column(db.Text)
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)

    bank = db.relationship("BankAccount", back_populates="entries")
    transfer = db.relationship("BankTransfer", back_populates="entries")
    created_by = db.relationship("User", foreign_keys=[created_by_id])

    @property
    def is_transfer(self) -> bool:
        return self.transfer_id is not None

    @property
    def type_label(self) -> str:
        if self.entry_type == self.TYPE_TRANSFER_IN:
            return "Transfer In"
        if self.entry_type == self.TYPE_TRANSFER_OUT:
            return "Transfer Out"
        return "Standard"

    @property
    def counterparty_bank(self):
        if not self.transfer:
            return None
        if self.entry_type == self.TYPE_TRANSFER_OUT:
            return self.transfer.to_bank
        if self.entry_type == self.TYPE_TRANSFER_IN:
            return self.transfer.from_bank
        return None


class BankTransfer(db.Model):
    """Cross-bank transfer — creates matching withdrawal and deposit entries."""

    __tablename__ = "bank_transfers"

    id = db.Column(db.Integer, primary_key=True)
    transfer_date = db.Column(db.Date, nullable=False)
    from_bank_id = db.Column(db.Integer, db.ForeignKey("bank_accounts.id"), nullable=False)
    to_bank_id = db.Column(db.Integer, db.ForeignKey("bank_accounts.id"), nullable=False)
    amount = db.Column(db.Float, nullable=False)
    reference = db.Column(db.String(100))
    notes = db.Column(db.Text)
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)

    from_bank = db.relationship("BankAccount", foreign_keys=[from_bank_id])
    to_bank = db.relationship("BankAccount", foreign_keys=[to_bank_id])
    entries = db.relationship(
        "BankLedgerEntry",
        back_populates="transfer",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    created_by = db.relationship("User", foreign_keys=[created_by_id])
