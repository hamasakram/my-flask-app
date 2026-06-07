from datetime import datetime, timezone

from flask_login import UserMixin
from werkzeug.security import check_password_hash, generate_password_hash

from app import db


def utcnow():
    return datetime.now(timezone.utc)


class Company(db.Model):
    __tablename__ = "companies"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), unique=True, nullable=False)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)

    ink_types = db.relationship("InkType", back_populates="company", lazy="dynamic")
    opening_stocks = db.relationship("OpeningStock", back_populates="company", lazy="dynamic")
    transactions = db.relationship("InventoryTransaction", back_populates="company", lazy="dynamic")


class InkType(db.Model):
    __tablename__ = "ink_types"

    id = db.Column(db.Integer, primary_key=True)
    company_id = db.Column(db.Integer, db.ForeignKey("companies.id"), nullable=False)
    name = db.Column(db.String(150), nullable=False)
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
    TRANSACTION_USED = "Stock Used"
    TRANSACTION_TYPES = (TRANSACTION_RECEIVED, TRANSACTION_USED)

    id = db.Column(db.Integer, primary_key=True)
    company_id = db.Column(db.Integer, db.ForeignKey("companies.id"), nullable=False)
    ink_type_id = db.Column(db.Integer, db.ForeignKey("ink_types.id"), nullable=False)
    transaction_type = db.Column(db.String(50), nullable=False)
    quantity = db.Column(db.Float, nullable=False)
    quantity_left = db.Column(db.Float, nullable=True)
    transaction_date = db.Column(db.Date, nullable=False)
    notes = db.Column(db.Text)
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)

    company = db.relationship("Company", back_populates="transactions")
    ink_type = db.relationship("InkType", back_populates="transactions")
    created_by = db.relationship("User", foreign_keys=[created_by_id])


class User(UserMixin, db.Model):
    __tablename__ = "users"

    ROLE_ADMIN = "admin"
    ROLE_MANAGER = "manager"
    ROLE_VIEWER = "viewer"
    ROLES = (ROLE_ADMIN, ROLE_MANAGER, ROLE_VIEWER)

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
