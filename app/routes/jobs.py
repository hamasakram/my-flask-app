"""Job Planning module: add jobs, check In Hall / Machine Hall material stock, track pending jobs."""

from flask import Blueprint, abort, flash, redirect, render_template, request, send_file, url_for
from flask_login import current_user, login_required

from app import db
from app.models import PrintJob
from app.services import print_jobs as jobs_service
from app.services.inventory import log_audit

jobs_bp = Blueprint("jobs", __name__, url_prefix="/jobs")


def require_edit_access():
    if not current_user.can_edit():
        abort(403)


def _get_job_or_404(job_id: int) -> PrintJob:
    job = db.session.get(PrintJob, job_id)
    if not job:
        abort(404)
    return job


def _form_context(job=None, values=None):
    material_catalog = jobs_service.build_material_catalog()
    return {
        "job": job,
        "values": values or jobs_service.job_form_values(job, material_catalog, {}),
        "material_groups": jobs_service.material_options(material_catalog),
        "material_slots": jobs_service.MATERIAL_SLOTS,
        "required_slots": jobs_service.REQUIRED_MATERIAL_SLOTS,
        "production_units": jobs_service.PRODUCTION_UNITS,
    }, material_catalog, None


def _job_summary(job: PrintJob) -> str:
    materials = ", ".join(line.material_name for line in job.materials)
    return f"{job.job_number} {job.job_name} | produce: {job.production_label or '-'} | materials: {materials}"


@jobs_bp.route("/")
@login_required
def dashboard():
    return render_template("jobs/dashboard.html", stats=jobs_service.dashboard_stats())


@jobs_bp.route("/new", methods=["GET", "POST"])
@login_required
def new_job():
    context, material_catalog, ink_catalog = _form_context()

    if request.method == "POST":
        require_edit_access()
        try:
            data = jobs_service.parse_job_form(request.form, material_catalog, ink_catalog)
            job = jobs_service.create_job(data, current_user.id)
            log_audit(current_user.id, "CREATE", "PrintJob", job.id, _job_summary(job))
            db.session.commit()
        except ValueError as exc:
            db.session.rollback()
            flash(str(exc), "danger")
            return redirect(url_for("jobs.new_job"))

        check = jobs_service.evaluate_job(job)
        if check["ready"]:
            flash(f"{job.job_number} saved. All materials are in stock.", "success")
        else:
            flash(
                f"{job.job_number} saved, but {check['problem_count']} material(s) are short or not in stock.",
                "warning",
            )
        return redirect(url_for("jobs.job_detail", job_id=job.id))

    return render_template("jobs/job_form.html", **context)


@jobs_bp.route("/added")
@login_required
def jobs_list():
    status = request.args.get("status", "").strip()
    search = request.args.get("q", "").strip()
    jobs = jobs_service.list_jobs(status or None, search or None)
    return render_template(
        "jobs/jobs_list.html",
        rows=jobs_service.evaluate_jobs(jobs),
        status=status,
        search=search,
        status_labels=PrintJob.STATUS_LABELS,
    )


@jobs_bp.route("/pending")
@login_required
def pending_jobs():
    rows = jobs_service.evaluate_jobs(jobs_service.list_open_jobs())
    return render_template(
        "jobs/pending_jobs.html",
        rows=rows,
        ready_count=sum(1 for row in rows if row["ready"]),
        short_count=sum(1 for row in rows if not row["ready"]),
    )


@jobs_bp.route("/<int:job_id>")
@login_required
def job_detail(job_id):
    job = _get_job_or_404(job_id)
    return render_template(
        "jobs/job_detail.html",
        check=jobs_service.evaluate_job(job),
        job=job,
        statuses=PrintJob.STATUS_LABELS,
        to_pkt=jobs_service.to_pkt,
    )


@jobs_bp.route("/<int:job_id>/edit", methods=["GET", "POST"])
@login_required
def edit_job(job_id):
    require_edit_access()
    job = _get_job_or_404(job_id)
    context, material_catalog, ink_catalog = _form_context(job)

    if request.method == "POST":
        try:
            data = jobs_service.parse_job_form(request.form, material_catalog, ink_catalog)
            jobs_service.update_job(job, data)
            log_audit(current_user.id, "UPDATE", "PrintJob", job.id, _job_summary(job))
            db.session.commit()
        except ValueError as exc:
            db.session.rollback()
            flash(str(exc), "danger")
            return redirect(url_for("jobs.edit_job", job_id=job.id))
        flash(f"{job.job_number} updated.", "success")
        return redirect(url_for("jobs.job_detail", job_id=job.id))

    return render_template("jobs/job_form.html", **context)


@jobs_bp.route("/<int:job_id>/status", methods=["POST"])
@login_required
def change_status(job_id):
    require_edit_access()
    job = _get_job_or_404(job_id)
    status = request.form.get("status", "").strip()
    try:
        old_label = job.status_label
        jobs_service.set_job_status(job, status)
        log_audit(
            current_user.id,
            "UPDATE",
            "PrintJob",
            job.id,
            f"{job.job_number} status {old_label} -> {job.status_label}",
        )
        db.session.commit()
    except ValueError as exc:
        db.session.rollback()
        flash(str(exc), "danger")
    else:
        flash(f"{job.job_number} marked {job.status_label}.", "success")

    next_url = request.form.get("next", "")
    if next_url.startswith("/jobs"):
        return redirect(next_url)
    return redirect(url_for("jobs.job_detail", job_id=job.id))


@jobs_bp.route("/<int:job_id>/delete", methods=["POST"])
@login_required
def delete_job(job_id):
    require_edit_access()
    job = _get_job_or_404(job_id)
    summary = _job_summary(job)
    number = job.job_number
    db.session.delete(job)
    log_audit(current_user.id, "DELETE", "PrintJob", job_id, summary)
    db.session.commit()
    flash(f"{number} deleted.", "success")
    return redirect(url_for("jobs.jobs_list"))


@jobs_bp.route("/<int:job_id>/pdf")
@login_required
def job_pdf(job_id):
    from app.services.print_jobs_pdf import generate_job_card_pdf

    job = _get_job_or_404(job_id)
    buffer = generate_job_card_pdf(jobs_service.evaluate_job(job))
    return send_file(
        buffer,
        mimetype="application/pdf",
        as_attachment=False,
        download_name=f"{job.job_number}_job_card.pdf",
    )


@jobs_bp.route("/pending/pdf")
@login_required
def pending_jobs_pdf():
    from app.services.print_jobs_pdf import generate_pending_jobs_pdf

    rows = jobs_service.evaluate_jobs(jobs_service.list_open_jobs())
    buffer = generate_pending_jobs_pdf(rows)
    stamp = jobs_service.pkt_today().strftime("%Y%m%d")
    return send_file(
        buffer,
        mimetype="application/pdf",
        as_attachment=False,
        download_name=f"pending_jobs_{stamp}.pdf",
    )
