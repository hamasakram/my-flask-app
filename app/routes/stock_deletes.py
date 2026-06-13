from flask import Blueprint, abort, flash, redirect, request, url_for
from flask_login import current_user, login_required

from app import db
from app.models import (
    ChemicalItem,
    ChemicalOpeningStock,
    ChemicalTransaction,
    Company,
    GlueItem,
    GlueOpeningStock,
    GlueTransaction,
    InventoryTransaction,
    Material,
    MaterialOpeningStock,
    MaterialTransaction,
    OpeningStock,
)
from app.services.inventory import log_audit
from app.services.record_delete import (
    chemical_item_in_use,
    chemicals_company_in_use,
    glue_company_in_use,
    glue_item_in_use,
    material_in_use,
    materials_company_in_use,
)

stock_deletes_bp = Blueprint("stock_deletes", __name__, url_prefix="/stock-delete")


def require_edit_access():
    if not current_user.can_edit():
        abort(403)


def _delete_entity(entity, entity_type: str, details: str, redirect_url: str):
    require_edit_access()
    entity_id = entity.id
    db.session.delete(entity)
    log_audit(current_user.id, "DELETE", entity_type, entity_id, details)
    db.session.commit()
    flash("Record deleted.", "success")
    return redirect(redirect_url)


# --- Ink transactions & opening ---


@stock_deletes_bp.route("/ink/received/<int:txn_id>", methods=["POST"])
@login_required
def delete_ink_received(txn_id):
    txn = InventoryTransaction.query.get_or_404(txn_id)
    if txn.transaction_type != InventoryTransaction.TRANSACTION_RECEIVED:
        abort(404)
    return _delete_entity(
        txn,
        "InventoryTransaction",
        f"Deleted received record #{txn_id}",
        url_for("inventory.receive_stock"),
    )


@stock_deletes_bp.route("/ink/used/<int:txn_id>", methods=["POST"])
@login_required
def delete_ink_used(txn_id):
    txn = InventoryTransaction.query.get_or_404(txn_id)
    if txn.transaction_type != InventoryTransaction.TRANSACTION_USED:
        abort(404)
    return _delete_entity(
        txn,
        "InventoryTransaction",
        f"Deleted usage record #{txn_id}",
        url_for("inventory.use_stock"),
    )


@stock_deletes_bp.route("/ink/opening/<int:record_id>", methods=["POST"])
@login_required
def delete_ink_opening(record_id):
    record = OpeningStock.query.get_or_404(record_id)
    return _delete_entity(
        record,
        "OpeningStock",
        f"Deleted opening stock #{record_id}",
        url_for("inventory.opening_stock"),
    )


# --- Materials ---


@stock_deletes_bp.route("/materials/received/<int:txn_id>", methods=["POST"])
@login_required
def delete_materials_received(txn_id):
    txn = MaterialTransaction.query.get_or_404(txn_id)
    if txn.transaction_type != MaterialTransaction.TRANSACTION_RECEIVED:
        abort(404)
    return _delete_entity(
        txn,
        "MaterialTransaction",
        f"Deleted purchase record #{txn_id}",
        url_for("materials.receive_stock"),
    )


@stock_deletes_bp.route("/materials/used/<int:txn_id>", methods=["POST"])
@login_required
def delete_materials_used(txn_id):
    txn = MaterialTransaction.query.get_or_404(txn_id)
    if txn.transaction_type != MaterialTransaction.TRANSACTION_USED:
        abort(404)
    return _delete_entity(
        txn,
        "MaterialTransaction",
        f"Deleted usage record #{txn_id}",
        url_for("materials.use_stock"),
    )


@stock_deletes_bp.route("/materials/opening/<int:record_id>", methods=["POST"])
@login_required
def delete_materials_opening(record_id):
    record = MaterialOpeningStock.query.get_or_404(record_id)
    return _delete_entity(
        record,
        "MaterialOpeningStock",
        f"Deleted opening stock #{record_id}",
        url_for("materials.opening_stock"),
    )


@stock_deletes_bp.route("/materials/company/<int:company_id>", methods=["POST"])
@login_required
def delete_materials_company(company_id):
    require_edit_access()
    company = Company.query.get_or_404(company_id)
    if company.scope != Company.SCOPE_MATERIALS:
        abort(404)
    if materials_company_in_use(company_id):
        flash("Cannot delete — this company has materials or stock records.", "danger")
        return redirect(url_for("materials.companies"))
    return _delete_entity(
        company,
        "Company",
        f"Deleted materials company: {company.name}",
        url_for("materials.companies"),
    )


@stock_deletes_bp.route("/materials/catalog/<int:material_id>", methods=["POST"])
@login_required
def delete_materials_catalog(material_id):
    require_edit_access()
    material = Material.query.get_or_404(material_id)
    if material_in_use(material_id):
        flash("Cannot delete — this material has stock or transaction records.", "danger")
        return redirect(url_for("materials.catalog"))
    return _delete_entity(
        material,
        "Material",
        f"Deleted material: {material.display_name}",
        url_for("materials.catalog"),
    )


# --- Glue ---


@stock_deletes_bp.route("/glue/received/<int:txn_id>", methods=["POST"])
@login_required
def delete_glue_received(txn_id):
    txn = GlueTransaction.query.get_or_404(txn_id)
    if txn.transaction_type != GlueTransaction.TRANSACTION_RECEIVED:
        abort(404)
    return _delete_entity(
        txn,
        "GlueTransaction",
        f"Deleted received record #{txn_id}",
        url_for("glue.receive_stock"),
    )


@stock_deletes_bp.route("/glue/used/<int:txn_id>", methods=["POST"])
@login_required
def delete_glue_used(txn_id):
    txn = GlueTransaction.query.get_or_404(txn_id)
    if txn.transaction_type != GlueTransaction.TRANSACTION_USED:
        abort(404)
    return _delete_entity(
        txn,
        "GlueTransaction",
        f"Deleted usage record #{txn_id}",
        url_for("glue.use_stock"),
    )


@stock_deletes_bp.route("/glue/opening/<int:record_id>", methods=["POST"])
@login_required
def delete_glue_opening(record_id):
    record = GlueOpeningStock.query.get_or_404(record_id)
    return _delete_entity(
        record,
        "GlueOpeningStock",
        f"Deleted opening stock #{record_id}",
        url_for("glue.opening_stock"),
    )


@stock_deletes_bp.route("/glue/company/<int:company_id>", methods=["POST"])
@login_required
def delete_glue_company(company_id):
    require_edit_access()
    company = Company.query.get_or_404(company_id)
    if company.scope != Company.SCOPE_GLUE:
        abort(404)
    if glue_company_in_use(company_id):
        flash("Cannot delete — this company has items or stock records.", "danger")
        return redirect(url_for("glue.companies"))
    return _delete_entity(
        company,
        "Company",
        f"Deleted glue company: {company.name}",
        url_for("glue.companies"),
    )


@stock_deletes_bp.route("/glue/catalog/<int:item_id>", methods=["POST"])
@login_required
def delete_glue_catalog(item_id):
    require_edit_access()
    item = GlueItem.query.get_or_404(item_id)
    if glue_item_in_use(item_id):
        flash("Cannot delete — this item has stock or transaction records.", "danger")
        return redirect(url_for("glue.catalog"))
    return _delete_entity(
        item,
        "GlueItem",
        f"Deleted glue item: {item.display_name}",
        url_for("glue.catalog"),
    )


# --- Chemicals ---


@stock_deletes_bp.route("/chemicals/received/<int:txn_id>", methods=["POST"])
@login_required
def delete_chemicals_received(txn_id):
    txn = ChemicalTransaction.query.get_or_404(txn_id)
    if txn.transaction_type != ChemicalTransaction.TRANSACTION_RECEIVED:
        abort(404)
    return _delete_entity(
        txn,
        "ChemicalTransaction",
        f"Deleted received record #{txn_id}",
        url_for("chemicals.receive_stock"),
    )


@stock_deletes_bp.route("/chemicals/used/<int:txn_id>", methods=["POST"])
@login_required
def delete_chemicals_used(txn_id):
    txn = ChemicalTransaction.query.get_or_404(txn_id)
    if txn.transaction_type != ChemicalTransaction.TRANSACTION_USED:
        abort(404)
    return _delete_entity(
        txn,
        "ChemicalTransaction",
        f"Deleted usage record #{txn_id}",
        url_for("chemicals.use_stock"),
    )


@stock_deletes_bp.route("/chemicals/opening/<int:record_id>", methods=["POST"])
@login_required
def delete_chemicals_opening(record_id):
    record = ChemicalOpeningStock.query.get_or_404(record_id)
    return _delete_entity(
        record,
        "ChemicalOpeningStock",
        f"Deleted opening stock #{record_id}",
        url_for("chemicals.opening_stock"),
    )


@stock_deletes_bp.route("/chemicals/company/<int:company_id>", methods=["POST"])
@login_required
def delete_chemicals_company(company_id):
    require_edit_access()
    company = Company.query.get_or_404(company_id)
    if company.scope != Company.SCOPE_CHEMICALS:
        abort(404)
    if chemicals_company_in_use(company_id):
        flash("Cannot delete — this company has items or stock records.", "danger")
        return redirect(url_for("chemicals.companies"))
    return _delete_entity(
        company,
        "Company",
        f"Deleted chemicals company: {company.name}",
        url_for("chemicals.companies"),
    )


@stock_deletes_bp.route("/chemicals/catalog/<int:item_id>", methods=["POST"])
@login_required
def delete_chemicals_catalog(item_id):
    require_edit_access()
    item = ChemicalItem.query.get_or_404(item_id)
    if chemical_item_in_use(item_id):
        flash("Cannot delete — this item has stock or transaction records.", "danger")
        return redirect(url_for("chemicals.catalog"))
    return _delete_entity(
        item,
        "ChemicalItem",
        f"Deleted chemical item: {item.display_name}",
        url_for("chemicals.catalog"),
    )
