from flask import Blueprint, redirect, render_template, request, send_file, url_for
from flask_login import login_required

from app.module_context import ALL_MODULES, MODULE_SH_TRADERS, module_dashboard_url, module_label
from app.services.pdf_builder import generate_custom_pdf, get_pdf_fields

pdf_bp = Blueprint("pdf", __name__, url_prefix="/pdf")


@pdf_bp.route("/builder", methods=["GET", "POST"])
@login_required
def builder():
    module = request.args.get("module") or request.form.get("module")
    if module not in ALL_MODULES:
        return redirect(url_for("auth.choose_module"))

    report_type = request.args.get("report_type") or request.form.get("report_type") or "purchases"
    fields = get_pdf_fields(module, report_type=report_type)
    companies = _companies_for_module(module, report_type=report_type)

    if request.method == "POST":
        selected = request.form.getlist("fields")
        company_id = request.form.get("company_id", type=int)
        report_type = request.form.get("report_type") or "purchases"
        if not selected:
            from flask import flash

            flash("Select at least one column for the PDF.", "danger")
            return redirect(url_for("pdf.builder", module=module, report_type=report_type))

        output = generate_custom_pdf(
            module, selected, company_id=company_id, report_type=report_type
        )
        filename = f"rn_colour_{module}_{report_type}_report.pdf"
        return send_file(
            output,
            as_attachment=True,
            download_name=filename,
            mimetype="application/pdf",
        )

    return render_template(
        "shared/pdf_builder.html",
        module=module,
        module_label=module_label(module),
        dashboard_url=module_dashboard_url(module),
        fields=fields,
        companies=companies,
        report_type=report_type,
        is_sh_module=module == MODULE_SH_TRADERS,
    )


def _companies_for_module(module, report_type="purchases"):
    from app.models import Company, ShSupplierCompany
    from app.services.companies import (
        get_chemical_companies,
        get_glue_companies,
        get_ink_companies,
        get_material_companies,
    )

    mapping = {
        "ink": get_ink_companies,
        "materials": get_material_companies,
        "glue": get_glue_companies,
        "chemicals": get_chemical_companies,
    }
    if module == MODULE_SH_TRADERS and report_type == "purchases":
        return ShSupplierCompany.query.order_by(ShSupplierCompany.name).all()
    getter = mapping.get(module)
    if getter:
        return getter()
    return Company.query.filter_by(is_active=True).all()
