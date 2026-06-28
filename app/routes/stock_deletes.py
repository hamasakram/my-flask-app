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
    BankAccount,
    BankLedgerEntry,
    BankTransfer,
    HomeLedgerEntry,
    HomeParty,
    InkType,
    InventoryTransaction,
    Material,
    MaterialOpeningStock,
    MaterialTransaction,
    OpeningStock,
    StockPurchaseReceipt,
    ShClientCompany,
    ShGatePass,
    ShSaleInvoice,
    ShLedgerEntry,
    ShOpeningBalance,
    ShPaymentScreenshot,
    ShPurchase,
    ShSupplierCompany,
)
from app.services.inventory import log_audit
from app.services.record_delete import (
    chemical_item_in_use,
    chemicals_company_in_use,
    glue_company_in_use,
    glue_item_in_use,
    ink_company_in_use,
    ink_type_in_use,
    material_in_use,
    materials_company_in_use,
)
from app.services.receipt_uploads import delete_receipt_file
from app.services.sh_uploads import delete_payment_screenshot
from app.services.bank_ledger import bank_has_transfers

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


@stock_deletes_bp.route("/ink/issued/<int:txn_id>", methods=["POST"])
@login_required
def delete_ink_issued(txn_id):
    txn = InventoryTransaction.query.get_or_404(txn_id)
    if txn.transaction_type != InventoryTransaction.TRANSACTION_ISSUED:
        abort(404)
    return _delete_entity(
        txn,
        "InventoryTransaction",
        f"Deleted issue record #{txn_id}",
        url_for("inventory.issue_to_use"),
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


@stock_deletes_bp.route("/ink/company/<int:company_id>", methods=["POST"])
@login_required
def delete_ink_company(company_id):
    require_edit_access()
    company = Company.query.get_or_404(company_id)
    if company.scope != Company.SCOPE_INK:
        abort(404)
    if ink_company_in_use(company_id):
        flash("Cannot delete — this company has inks or stock records.", "danger")
        return redirect(url_for("inventory.companies"))
    return _delete_entity(
        company,
        "Company",
        f"Deleted ink company: {company.name}",
        url_for("inventory.companies"),
    )


@stock_deletes_bp.route("/ink/catalog/<int:ink_id>", methods=["POST"])
@login_required
def delete_ink_catalog(ink_id):
    require_edit_access()
    ink = InkType.query.get_or_404(ink_id)
    if ink_type_in_use(ink_id):
        flash("Cannot delete — this ink has opening stock or transaction records.", "danger")
        return redirect(url_for("inventory.catalog"))
    return _delete_entity(
        ink,
        "InkType",
        f"Deleted ink: {ink.name}",
        url_for("inventory.catalog"),
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


# --- SH Traders ---


@stock_deletes_bp.route("/sh/supplier/<int:company_id>", methods=["POST"])
@login_required
def delete_sh_supplier(company_id):
    require_edit_access()
    company = ShSupplierCompany.query.get_or_404(company_id)
    if company.purchases.count() > 0:
        flash("Cannot delete — this supplier has purchase records.", "danger")
        return redirect(url_for("sh_main.suppliers"))
    return _delete_entity(
        company,
        "ShSupplierCompany",
        f"Deleted SH supplier: {company.name}",
        url_for("sh_main.suppliers"),
    )


@stock_deletes_bp.route("/sh/client/<int:company_id>", methods=["POST"])
@login_required
def delete_sh_client(company_id):
    require_edit_access()
    company = ShClientCompany.query.get_or_404(company_id)
    if company.purchases.count() > 0:
        flash("Cannot delete — this client has purchase records.", "danger")
        return redirect(url_for("sh_main.clients"))
    return _delete_entity(
        company,
        "ShClientCompany",
        f"Deleted SH client: {company.name}",
        url_for("sh_main.clients"),
    )


@stock_deletes_bp.route("/sh/purchase/<int:purchase_id>", methods=["POST"])
@login_required
def delete_sh_purchase(purchase_id):
    purchase = ShPurchase.query.get_or_404(purchase_id)
    ShPaymentScreenshot.query.filter_by(purchase_id=purchase_id).update(
        {ShPaymentScreenshot.purchase_id: None},
        synchronize_session=False,
    )
    ShGatePass.query.filter_by(purchase_id=purchase_id).update(
        {ShGatePass.purchase_id: None},
        synchronize_session=False,
    )
    return _delete_entity(
        purchase,
        "ShPurchase",
        f"Deleted SH purchase #{purchase_id}",
        url_for("sh_main.purchases"),
    )


@stock_deletes_bp.route("/sh/ledger/<int:entry_id>", methods=["POST"])
@login_required
def delete_sh_ledger(entry_id):
    entry = ShLedgerEntry.query.get_or_404(entry_id)
    return _delete_entity(
        entry,
        "ShLedgerEntry",
        f"Deleted SH ledger entry #{entry_id}",
        url_for("sh_main.payments"),
    )


@stock_deletes_bp.route("/sh/payment-screenshot/<int:record_id>", methods=["POST"])
@login_required
def delete_sh_payment_screenshot(record_id):
    require_edit_access()
    record = ShPaymentScreenshot.query.get_or_404(record_id)
    filename = record.screenshot_filename
    db.session.delete(record)
    log_audit(
        current_user.id,
        "DELETE",
        "ShPaymentScreenshot",
        record_id,
        f"Deleted payment screenshot #{record_id}",
    )
    db.session.commit()
    delete_payment_screenshot(filename)
    flash("Payment screenshot deleted.", "success")
    return redirect(url_for("sh_main.payment_screenshots"))


@stock_deletes_bp.route("/sh/sale-invoice/<int:invoice_id>", methods=["POST"])
@login_required
def delete_sh_sale_invoice(invoice_id):
    invoice = ShSaleInvoice.query.get_or_404(invoice_id)
    return _delete_entity(
        invoice,
        "ShSaleInvoice",
        f"Deleted sale invoice {invoice.invoice_number}",
        url_for("sh_main.sale_invoices"),
    )


@stock_deletes_bp.route("/sh/gate-pass/<int:gate_pass_id>", methods=["POST"])
@login_required
def delete_sh_gate_pass(gate_pass_id):
    gate_pass = ShGatePass.query.get_or_404(gate_pass_id)
    return _delete_entity(
        gate_pass,
        "ShGatePass",
        f"Deleted gate pass {gate_pass.gate_pass_number}",
        url_for("sh_main.gate_passes"),
    )


# --- Home Ledger ---


@stock_deletes_bp.route("/home/party/<int:party_id>", methods=["POST"])
@login_required
def delete_home_party(party_id):
    party = HomeParty.query.get_or_404(party_id)
    return _delete_entity(
        party,
        "HomeParty",
        f"Deleted home party: {party.name}",
        url_for("home_ledger.parties"),
    )


@stock_deletes_bp.route("/home/ledger/<int:entry_id>", methods=["POST"])
@login_required
def delete_home_ledger_entry(entry_id):
    entry = HomeLedgerEntry.query.get_or_404(entry_id)
    party_id = entry.party_id
    return _delete_entity(
        entry,
        "HomeLedgerEntry",
        f"Deleted home ledger entry #{entry_id}",
        url_for("home_ledger.party_ledger", party_id=party_id),
    )


# --- Bank Ledger ---


@stock_deletes_bp.route("/bank/account/<int:bank_id>", methods=["POST"])
@login_required
def delete_bank_account(bank_id):
    bank = BankAccount.query.get_or_404(bank_id)
    if bank_has_transfers(bank_id):
        flash("Cannot delete — this bank has cross-bank transfer records.", "danger")
        return redirect(url_for("bank_ledger.banks"))
    return _delete_entity(
        bank,
        "BankAccount",
        f"Deleted bank account: {bank.display_name}",
        url_for("bank_ledger.banks"),
    )


@stock_deletes_bp.route("/bank/ledger/<int:entry_id>", methods=["POST"])
@login_required
def delete_bank_ledger_entry(entry_id):
    entry = BankLedgerEntry.query.get_or_404(entry_id)
    if entry.is_transfer:
        transfer_id = entry.transfer_id
        transfer = BankTransfer.query.get_or_404(transfer_id)
        from_bank_id = transfer.from_bank_id
        db.session.delete(transfer)
        log_audit(
            current_user.id,
            "DELETE",
            "BankTransfer",
            transfer_id,
            f"Deleted bank transfer #{transfer_id}",
        )
        db.session.commit()
        flash("Transfer deleted from both bank ledgers.", "success")
        return redirect(url_for("bank_ledger.bank_ledger", bank_id=from_bank_id))

    bank_id = entry.bank_id
    return _delete_entity(
        entry,
        "BankLedgerEntry",
        f"Deleted bank ledger entry #{entry_id}",
        url_for("bank_ledger.bank_ledger", bank_id=bank_id),
    )


@stock_deletes_bp.route("/bank/transfer/<int:transfer_id>", methods=["POST"])
@login_required
def delete_bank_transfer(transfer_id):
    transfer = BankTransfer.query.get_or_404(transfer_id)
    from_bank_id = transfer.from_bank_id
    db.session.delete(transfer)
    log_audit(
        current_user.id,
        "DELETE",
        "BankTransfer",
        transfer_id,
        f"Deleted bank transfer #{transfer_id}",
    )
    db.session.commit()
    flash("Transfer deleted from both bank ledgers.", "success")
    return redirect(url_for("bank_ledger.transfers"))


# --- Purchase Receipts ---


def _delete_purchase_receipt(record_id, module, redirect_endpoint):
    require_edit_access()
    record = StockPurchaseReceipt.query.filter_by(id=record_id, module=module).first_or_404()
    filename = record.screenshot_filename
    db.session.delete(record)
    log_audit(
        current_user.id,
        "DELETE",
        "StockPurchaseReceipt",
        record_id,
        f"Deleted purchase receipt #{record_id}",
    )
    db.session.commit()
    delete_receipt_file(filename)
    flash("Purchase receipt deleted.", "success")
    return redirect(url_for(redirect_endpoint))


@stock_deletes_bp.route("/ink/purchase-receipt/<int:record_id>", methods=["POST"])
@login_required
def delete_ink_purchase_receipt(record_id):
    return _delete_purchase_receipt(
        record_id, StockPurchaseReceipt.MODULE_INK, "inventory.purchase_receipts"
    )


@stock_deletes_bp.route("/materials/purchase-receipt/<int:record_id>", methods=["POST"])
@login_required
def delete_materials_purchase_receipt(record_id):
    return _delete_purchase_receipt(
        record_id, StockPurchaseReceipt.MODULE_MATERIALS, "materials.purchase_receipts"
    )
