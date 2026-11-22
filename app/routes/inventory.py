from datetime import date, datetime

from flask import Blueprint, abort, flash, jsonify, redirect, render_template, request, send_file, url_for
from flask_login import current_user, login_required
from sqlalchemy import literal
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from app import db
from app.models import InkType, InventoryTransaction
from app.services.ink_inventory import (
    calculate_live_stock,
    calculate_remaining_from_out,
    create_ink,
    get_current_cans,
    get_ink_options,
)
from app.services.ink_export import export_used_inks_pdf
from app.services.ink_used_stock import (
    create_catalog_ink_name,
    create_catalog_shade_name,
    get_used_ink_report_data,
    list_used_ink_name_records,
    list_used_ink_names,
    list_used_ink_shade_records,
    list_used_ink_shades,
    record_used_ink_stock,
    record_used_ink_usage,
    sync_used_ink_from_cans_out,
)
from app.services.inventory import log_audit

inventory_bp = Blueprint("inventory", __name__, url_prefix="/inventory")


def require_edit_access():
    if not current_user.can_edit():
        abort(403)


@inventory_bp.route("/inks", methods=["GET", "POST"])
@login_required
def inks_list():
    if request.method == "POST":
        require_edit_access()
        ink_company = request.form.get("ink_company", "").strip()
        color_code = request.form.get("color_code", "").strip()
        color = request.form.get("color", "").strip()
        total_cans = request.form.get("total_cans", type=float) or 0
        weight_per_can = request.form.get("weight_per_can", type=float) or 0

        try:
            ink = create_ink(ink_company, color_code, color, total_cans, weight_per_can)
            total_weight = total_cans * weight_per_can
            log_audit(
                current_user.id,
                "CREATE",
                "InkType",
                ink.id,
                f"Ink added: {ink.display_name} ({total_cans} cans, {total_weight:.1f} kg)",
            )
            db.session.commit()
            flash(f"Ink '{ink.display_name}' added.", "success")
        except ValueError as exc:
            db.session.rollback()
            flash(str(exc), "danger")
        except IntegrityError:
            db.session.rollback()
            flash("Could not save ink — a similar record may already exist.", "danger")
        except SQLAlchemyError:
            db.session.rollback()
            flash("Could not save ink due to a database error. Please try again.", "danger")
        return redirect(url_for("inventory.inks_list"))

    from app.models import OpeningStock
    from app.services.ink_stock_pdf import fmt_qty, summarize_ink_stock

    summary = summarize_ink_stock(calculate_live_stock())
    # Inks with Cans In / Cans Out (or opening) records can't be deleted; hide Delete for them.
    # Both id lists come back in one round-trip (UNION ALL, tagged by source).
    used_ids = (
        db.session.query(literal(1).label("src"), InventoryTransaction.ink_type_id.label("ink_id"))
        .distinct()
        .union_all(
            db.session.query(literal(2).label("src"), OpeningStock.ink_type_id.label("ink_id")).distinct()
        )
        .all()
    )
    in_use = {ink_id for src, ink_id in used_ids if src == 1 and ink_id is not None} | {
        ink_id for src, ink_id in used_ids if src == 2
    }
    companies = sorted({g["name"] for g in summary["groups"]} | {"RL", "Horizon Inks"}, key=str.lower)
    colours = sorted({r["colour"] for r in summary["rows"] if r["colour"]}, key=str.lower)
    existing = [
        {"company": r["company"].lower(), "code": r["code"].lower(), "colour": r["colour"].lower()}
        for r in summary["rows"]
    ]
    return render_template(
        "ink/inks.html",
        summary=summary,
        in_use=in_use,
        companies=companies,
        colours=colours,
        existing=existing,
        qty=fmt_qty,
    )


@inventory_bp.route("/current-stock")
@login_required
def current_stock():
    from app.services.ink_stock_pdf import fmt_qty, summarize_ink_stock

    summary = summarize_ink_stock(calculate_live_stock())
    return render_template("ink/current_stock.html", summary=summary, qty=fmt_qty)


@inventory_bp.route("/current-stock/pdf")
@login_required
def current_stock_pdf():
    from app.services.ink_stock_pdf import PAKISTAN_TZ, generate_ink_stock_pdf, summarize_ink_stock

    output = generate_ink_stock_pdf(summarize_ink_stock(calculate_live_stock()))
    filename = f"ink_hall_stock_{datetime.now(PAKISTAN_TZ).strftime('%Y-%m-%d')}.pdf"
    return send_file(output, as_attachment=True, download_name=filename, mimetype="application/pdf")


@inventory_bp.route("/cans-in", methods=["GET", "POST"])
@login_required
def cans_in():
    if request.method == "POST":
        require_edit_access()
        ink_type_id = request.form.get("ink_type_id", type=int)
        quantity = request.form.get("quantity", type=float)
        transaction_date = request.form.get("transaction_date")
        notes = request.form.get("notes", "").strip()

        if not ink_type_id or not quantity or quantity <= 0 or not transaction_date:
            flash("Ink, number of cans, and date are required.", "danger")
            return redirect(url_for("inventory.cans_in"))

        ink = db.session.get(InkType, ink_type_id)
        if not ink:
            flash("Invalid ink.", "danger")
            return redirect(url_for("inventory.cans_in"))

        parsed_date = datetime.strptime(transaction_date, "%Y-%m-%d").date()
        txn = InventoryTransaction(
            company_id=ink.company_id,
            ink_type_id=ink.id,
            transaction_type=InventoryTransaction.TRANSACTION_CANS_IN,
            quantity=quantity,
            transaction_date=parsed_date,
            notes=notes,
            created_by_id=current_user.id,
        )
        db.session.add(txn)
        db.session.flush()
        log_audit(
            current_user.id,
            "CREATE",
            "InventoryTransaction",
            txn.id,
            f"Cans In {quantity} of {ink.display_name}",
        )
        db.session.commit()
        flash(f"Cans In recorded: {quantity} cans of '{ink.display_name}'.", "success")
        return redirect(url_for("inventory.cans_in"))

    recent = (
        InventoryTransaction.query.filter_by(
            transaction_type=InventoryTransaction.TRANSACTION_CANS_IN
        )
        .order_by(InventoryTransaction.transaction_date.desc(), InventoryTransaction.id.desc())
        .limit(20)
        .all()
    )
    ink_stock = calculate_live_stock()
    return render_template(
        "ink/cans_in.html",
        # calculate_live_stock lists every ink in list_inks() order: no second ink query.
        inks=[row["ink"] for row in ink_stock],
        recent=recent,
        ink_stock=ink_stock,
    )


@inventory_bp.route("/cans-out", methods=["GET", "POST"])
@login_required
def cans_out():
    from datetime import timedelta, timezone

    # Pakistan has no daylight saving: "today" is always UTC+5 (the server runs on UTC).
    pk_today = datetime.now(timezone(timedelta(hours=5))).date()
    selected_date = request.args.get("date") or request.form.get("transaction_date")
    if selected_date:
        filter_date = datetime.strptime(selected_date, "%Y-%m-%d").date()
    else:
        filter_date = pk_today

    if request.method == "POST":
        require_edit_access()
        ink_type_id = request.form.get("ink_type_id", type=int)
        cans_out_qty = request.form.get("cans_out", type=float)
        where_used = request.form.get("where_used", "").strip()
        transaction_date = request.form.get("transaction_date")
        notes = request.form.get("notes", "").strip()
        link_used_ink = request.form.get("link_used_ink") == "on"
        used_ink_name = request.form.get("used_ink_name", "").strip()
        used_ink_shade = request.form.get("used_ink_shade", "").strip()

        redirect_date = transaction_date or filter_date.isoformat()
        if ink_type_id is None or cans_out_qty is None or cans_out_qty <= 0 or not transaction_date:
            flash("Ink, cans out, and date are required.", "danger")
            return redirect(url_for("inventory.cans_out", date=redirect_date))

        ink = db.session.get(InkType, ink_type_id)
        if not ink:
            flash("Invalid ink.", "danger")
            return redirect(url_for("inventory.cans_out", date=redirect_date))

        try:
            quantity_used, quantity_left = calculate_remaining_from_out(ink.id, cans_out_qty)
        except ValueError as exc:
            flash(str(exc), "danger")
            return redirect(url_for("inventory.cans_out", date=redirect_date))

        parsed_date = datetime.strptime(transaction_date, "%Y-%m-%d").date()
        txn = InventoryTransaction(
            company_id=ink.company_id,
            ink_type_id=ink.id,
            transaction_type=InventoryTransaction.TRANSACTION_CANS_OUT,
            quantity=quantity_used,
            quantity_left=quantity_left,
            where_used=where_used,
            transaction_date=parsed_date,
            notes=notes,
            created_by_id=current_user.id,
        )
        db.session.add(txn)
        db.session.flush()

        sync_used_ink_from_cans_out(
            ink,
            quantity_used,
            parsed_date,
            txn.id,
            ink_name=used_ink_name or ink.name,
            shade_name=used_ink_shade if used_ink_shade else (ink.color_code or ""),
            notes=notes or where_used,
            created_by_id=current_user.id,
        )

        log_audit(
            current_user.id,
            "CREATE",
            "InventoryTransaction",
            txn.id,
            f"Cans Out {quantity_used} of {ink.display_name}",
        )
        db.session.commit()
        flash(
            f"Cans Out recorded: {quantity_used:.1f} cans ({quantity_used * float(ink.weight_per_can):.1f} kg added to Used Ink Stock).",
            "success",
        )
        return redirect(url_for("inventory.cans_out", date=parsed_date.isoformat()))

    recent = (
        InventoryTransaction.query.filter(
            InventoryTransaction.transaction_type.in_(
                (InventoryTransaction.TRANSACTION_CANS_OUT, "Cans Left")
            ),
            InventoryTransaction.transaction_date == filter_date,
        )
        .order_by(InventoryTransaction.id.desc())
        .all()
    )
    report_data = get_used_ink_report_data(filter_date)
    ink_stock = calculate_live_stock()
    return render_template(
        "ink/cans_out.html",
        # calculate_live_stock lists every ink in list_inks() order: no second ink query.
        inks=[row["ink"] for row in ink_stock],
        recent=recent,
        report_data=report_data,
        selected_date=filter_date.isoformat(),
        ink_names=list_used_ink_names(),
        shade_names=list_used_ink_shades(),
        ink_stock=ink_stock,
        prev_date=(filter_date - timedelta(days=1)).isoformat(),
        next_date=(filter_date + timedelta(days=1)).isoformat(),
        today_date=pk_today.isoformat(),
    )


@inventory_bp.route("/cans-left")
@login_required
def cans_left_redirect():
    return redirect(url_for("inventory.cans_out"))


@inventory_bp.route("/used-ink-names-setup", methods=["GET", "POST"])
@login_required
def used_ink_names_setup():
    if request.method == "POST":
        require_edit_access()
        form_type = request.form.get("form_type", "")
        try:
            if form_type == "ink_name":
                name = create_catalog_ink_name(request.form.get("ink_name", ""))
                log_audit(
                    current_user.id,
                    "CREATE",
                    "UsedInkName",
                    name.id,
                    f"Added used ink name: {name.name}",
                )
                flash(f"Ink name '{name.name}' added.", "success")
            elif form_type == "shade_name":
                name = create_catalog_shade_name(request.form.get("shade_name", ""))
                log_audit(
                    current_user.id,
                    "CREATE",
                    "UsedInkShade",
                    name.id,
                    f"Added used ink shade: {name.name}",
                )
                flash(f"Shade name '{name.name}' added.", "success")
            db.session.commit()
        except ValueError as exc:
            db.session.rollback()
            flash(str(exc), "danger")
        except SQLAlchemyError:
            db.session.rollback()
            flash("Could not save name.", "danger")
        return redirect(url_for("inventory.used_ink_names_setup"))

    # How many Used Inks Stock entries use each name / shade (lower-case keys), so the page can
    # show which ones are in use (those can't be deleted).
    from app.models import UsedInkStock

    ink_name_use: dict[str, int] = {}
    shade_name_use: dict[str, int] = {}
    for used_ink, used_shade in db.session.query(UsedInkStock.ink_name, UsedInkStock.shade_name):
        if used_ink:
            ink_name_use[used_ink.lower()] = ink_name_use.get(used_ink.lower(), 0) + 1
        if used_shade:
            shade_name_use[used_shade.lower()] = shade_name_use.get(used_shade.lower(), 0) + 1

    return render_template(
        "ink/used_ink_names_setup.html",
        ink_names=list_used_ink_name_records(),
        shade_names=list_used_ink_shade_records(),
        ink_name_use=ink_name_use,
        shade_name_use=shade_name_use,
    )


@inventory_bp.route("/used-inks-stock", methods=["GET", "POST"])
@login_required
def used_inks_stock():
    selected_date = request.args.get("date") or request.form.get("entry_date")
    if selected_date:
        filter_date = datetime.strptime(selected_date, "%Y-%m-%d").date()
    else:
        filter_date = date.today()

    if request.method == "POST":
        require_edit_access()
        form_type = request.form.get("form_type", "add")
        ink_name = request.form.get("ink_name", "").strip()
        shade_name = request.form.get("shade_name", "").strip()
        quantity_kg = request.form.get("quantity_kg", type=float)
        entry_date = request.form.get("entry_date")
        notes = request.form.get("notes", "").strip()

        if not ink_name or quantity_kg is None or quantity_kg <= 0 or not entry_date:
            flash("Ink name, quantity (kg), and date are required.", "danger")
            return redirect(url_for("inventory.used_inks_stock", date=filter_date.isoformat()))

        parsed_date = datetime.strptime(entry_date, "%Y-%m-%d").date()
        try:
            if form_type == "use":
                record_used_ink_usage(
                    ink_name=ink_name,
                    shade_name=shade_name,
                    quantity_kg=quantity_kg,
                    entry_date=parsed_date,
                    notes=notes,
                    created_by_id=current_user.id,
                )
                action_label = f"Used {quantity_kg:.1f} kg"
            else:
                record_used_ink_stock(
                    ink_name=ink_name,
                    shade_name=shade_name,
                    quantity_kg=quantity_kg,
                    entry_date=parsed_date,
                    notes=notes,
                    created_by_id=current_user.id,
                )
                action_label = f"Added {quantity_kg:.1f} kg"

            log_audit(
                current_user.id,
                "CREATE",
                "UsedInkStock",
                None,
                f"Used ink stock: {ink_name} / {shade_name or '—'} — {action_label}",
            )
            db.session.commit()
            flash(f"Used ink stock updated ({action_label}).", "success")
        except ValueError as exc:
            db.session.rollback()
            flash(str(exc), "danger")
        return redirect(url_for("inventory.used_inks_stock", date=parsed_date.isoformat()))

    report_data = get_used_ink_report_data(filter_date)
    from datetime import timedelta

    return render_template(
        "ink/used_inks_stock.html",
        entries=report_data["entries"],
        report_data=report_data,
        balances=report_data["balances"],
        selected_date=filter_date.isoformat(),
        filter_date=filter_date,
        prev_date=(filter_date - timedelta(days=1)).isoformat(),
        next_date=(filter_date + timedelta(days=1)).isoformat(),
        date_in_url=bool(request.args.get("date")),
        ink_names=list_used_ink_names(),
        shade_names=list_used_ink_shades(),
    )


@inventory_bp.route("/used-inks-stock/pdf")
@login_required
def used_inks_stock_pdf():
    selected_date = request.args.get("date")
    filter_date = datetime.strptime(selected_date, "%Y-%m-%d").date() if selected_date else None
    report_data = get_used_ink_report_data(filter_date)
    output = export_used_inks_pdf(report_data)
    if filter_date:
        filename = f"used_inks_stock_{filter_date.strftime('%Y-%m-%d')}.pdf"
    else:
        filename = "used_inks_stock_all.pdf"
    return send_file(
        output,
        as_attachment=True,
        download_name=filename,
        mimetype="application/pdf",
    )


@inventory_bp.route("/api/inks")
@login_required
def api_inks():
    return jsonify(get_ink_options())


@inventory_bp.route("/api/stock/<int:ink_type_id>")
@login_required
def api_stock(ink_type_id):
    ink = db.session.get(InkType, ink_type_id)
    if not ink:
        return jsonify({"error": "Ink not found"}), 404
    current_cans = get_current_cans(ink)
    return jsonify(
        {
            "current_cans": current_cans,
            "current_weight": current_cans * float(ink.weight_per_can),
            "ink_name": ink.display_name,
        }
    )
