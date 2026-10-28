"""Job Planning: print jobs, their materials and inks, and live stock checks.

Materials come only from the two hall stock books, and their KG is added up:
  * In Hall stock (HallStockItem, location in_hall)
  * Machine Hall stock (HallStockItem, location machine_hall)
Items are matched by a normalised (name, size, micron) key, so the same film entered
in both halls lines up. Printing Materials stock is not used. Inks are checked
against the Ink stock (live cans / KG).
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone

from app import db
from app.models import HallStockItem, InkType, PrintJob, PrintJobInk, PrintJobMaterial
from app.services import ink_inventory

PKT = timezone(timedelta(hours=5), name="PKT")
NEW_CHOICE = "__new__"
MATERIAL_SLOTS = (1, 2, 3)
REQUIRED_MATERIAL_SLOTS = (1, 2)
PRODUCTION_UNITS = ("KG", "Pcs", "Meters", "Rolls")
EPSILON = 0.0005

STATUS_OK = "ok"
STATUS_SHORT = "short"
STATUS_OUT = "out"
STATUS_UNLISTED = "unlisted"
LINE_STATUS_LABELS = {
    STATUS_OK: "In Stock",
    STATUS_SHORT: "Short",
    STATUS_OUT: "Not in Stock",
    STATUS_UNLISTED: "Not in Stock",
}


# --------------------------------------------------------------------------- time


def pkt_now() -> datetime:
    return datetime.now(PKT)


def pkt_today() -> date:
    return pkt_now().date()


def to_pkt(value: datetime | None) -> datetime | None:
    """Stored timestamps are UTC (naive on SQLite/Postgres); show them in Pakistan time."""
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(PKT)


# --------------------------------------------------------------------------- keys


def _clean(value) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _norm_text(value) -> str:
    return _clean(value).casefold()


def _norm_size(value) -> str:
    return re.sub(r"\s+", "", _norm_text(value))


def _norm_micron(value) -> str:
    text = _norm_text(value)
    text = re.sub(r"(microns?|mic|μ|µ|u)$", "", text).strip()
    return re.sub(r"\s+", "", text)


def material_key(name: str, size: str = "", micron: str = "") -> str:
    return f"{_norm_text(name)}|{_norm_size(size)}|{_norm_micron(micron)}"


def format_micron(micron: str) -> str:
    cleaned = _clean(micron)
    if not cleaned:
        return ""
    if cleaned[-1].isdigit():
        return f"{cleaned}μ"
    return cleaned


def material_label(category: str, name: str, size: str = "", micron: str = "") -> str:
    parts = [_clean(category), _clean(name), _clean(size), format_micron(micron)]
    seen = []
    for part in parts:
        if part and part.casefold() not in (p.casefold() for p in seen):
            seen.append(part)
    return " · ".join(seen)


def ink_label(company: str, name: str, shade: str = "") -> str:
    return " · ".join(p for p in (_clean(company), _clean(shade), _clean(name)) if p)


def manual_ink_key(company: str, name: str, shade: str = "") -> str:
    return f"manual:{_norm_text(company)}|{_norm_text(name)}|{_norm_text(shade)}"


# --------------------------------------------------------------------------- catalogs


def _blank_material_entry(key, category, name, size, micron) -> dict:
    return {
        "key": key,
        "category": _clean(category),
        "name": _clean(name),
        "size": _clean(size),
        "micron": _clean(micron),
        "in_hall_kg": 0.0,
        "in_hall_rolls": 0.0,
        "machine_kg": 0.0,
        "machine_rolls": 0.0,
        "listed": False,
    }


def build_material_catalog() -> dict[str, dict]:
    """In Hall and Machine Hall materials keyed by material_key, with live KG and rolls."""
    entries: dict[str, dict] = {}

    def entry_for(category, name, size, micron) -> dict:
        key = material_key(name, size, micron)
        if key not in entries:
            entries[key] = _blank_material_entry(key, category, name, size, micron)
        elif not entries[key]["category"] and _clean(category):
            entries[key]["category"] = _clean(category)
        return entries[key]

    for item in HallStockItem.query.all():
        entry = entry_for(item.material_type, item.material_name, item.size, item.micron)
        kg = max(0.0, float(item.kg or 0))
        rolls = max(0.0, float(item.rolls_left or 0))
        if item.location == HallStockItem.LOCATION_MACHINE:
            entry["machine_kg"] += kg
            entry["machine_rolls"] += rolls
        else:
            entry["in_hall_kg"] += kg
            entry["in_hall_rolls"] += rolls
        entry["listed"] = True

    for entry in entries.values():
        entry["total_kg"] = entry["in_hall_kg"] + entry["machine_kg"]
        entry["in_stock"] = entry["total_kg"] > EPSILON
        entry["label"] = material_label(
            entry["category"], entry["name"], entry["size"], entry["micron"]
        )
    return entries


def material_options(catalog: dict[str, dict]) -> list[dict]:
    """Dropdown groups: in-stock materials grouped by category, then everything else."""
    in_stock: dict[str, list] = {}
    others: list = []
    for entry in sorted(catalog.values(), key=lambda e: e["label"].casefold()):
        if entry["in_stock"]:
            group = entry["category"] or "Other"
            in_stock.setdefault(group, []).append(entry)
        else:
            others.append(entry)
    groups = [
        {"label": f"In Stock — {name}", "options": in_stock[name]}
        for name in sorted(in_stock, key=str.casefold)
    ]
    if others:
        groups.append({"label": "Zero balance in In Hall / Machine Hall", "options": others})
    return groups


def build_ink_catalog() -> dict[str, dict]:
    """Inks keyed for dropdowns: 'ink:<id>' for stock inks, 'manual:...' for typed ones."""
    entries: dict[str, dict] = {}
    for row in ink_inventory.calculate_live_stock():
        ink = row["ink"]
        weight_per_can = float(ink.weight_per_can or 0)
        key = f"ink:{ink.id}"
        entries[key] = {
            "key": key,
            "ink_type_id": ink.id,
            "company": _clean(ink.ink_company),
            "name": _clean(ink.name),
            "shade": _clean(ink.color_code),
            "cans": max(0.0, float(row["current_cans"] or 0)),
            "kg": max(0.0, float(row["current_weight"] or 0)),
            "weight_per_can": weight_per_can,
            "listed": True,
        }

    for line in PrintJobInk.query.filter(PrintJobInk.ink_type_id.is_(None)).all():
        key = manual_ink_key(line.ink_company, line.ink_name, line.shade)
        if key not in entries:
            entries[key] = {
                "key": key,
                "ink_type_id": None,
                "company": _clean(line.ink_company),
                "name": _clean(line.ink_name),
                "shade": _clean(line.shade),
                "cans": 0.0,
                "kg": 0.0,
                "weight_per_can": 0.0,
                "listed": False,
            }

    for entry in entries.values():
        entry["label"] = ink_label(entry["company"], entry["name"], entry["shade"])
        entry["in_stock"] = entry["cans"] > EPSILON
    return entries


def ink_options(catalog: dict[str, dict]) -> list[dict]:
    in_stock: dict[str, list] = {}
    others: list = []
    for entry in sorted(catalog.values(), key=lambda e: e["label"].casefold()):
        if entry["in_stock"]:
            in_stock.setdefault(entry["company"] or "Other", []).append(entry)
        else:
            others.append(entry)
    groups = [
        {"label": f"In Stock — {name}", "options": in_stock[name]}
        for name in sorted(in_stock, key=str.casefold)
    ]
    if others:
        groups.append({"label": "Not in Stock (zero cans or typed on earlier jobs)", "options": others})
    return groups


# --------------------------------------------------------------------------- stock check


def _line_status(available: float, required, *, listed: bool) -> tuple[str, float]:
    if not listed:
        return STATUS_UNLISTED, float(required or 0)
    if available <= EPSILON:
        return STATUS_OUT, float(required or 0)
    if required and available + EPSILON < float(required):
        return STATUS_SHORT, float(required) - available
    return STATUS_OK, 0.0


def check_material_line(line: PrintJobMaterial, catalog: dict[str, dict]) -> dict:
    entry = catalog.get(line.material_key)
    in_hall = entry["in_hall_kg"] if entry else 0.0
    machine = entry["machine_kg"] if entry else 0.0
    total = in_hall + machine
    listed = bool(entry and entry["listed"])
    status, shortfall = _line_status(total, line.required_kg, listed=listed)
    return {
        "line": line,
        "position": line.position,
        "label": material_label(line.category, line.material_name, line.size, line.micron),
        "category": line.category or "",
        "name": line.material_name,
        "size": line.size or "",
        "micron": line.micron or "",
        "required_kg": line.required_kg,
        "in_hall_kg": in_hall,
        "in_hall_rolls": entry["in_hall_rolls"] if entry else 0.0,
        "machine_kg": machine,
        "machine_rolls": entry["machine_rolls"] if entry else 0.0,
        "total_kg": total,
        "status": status,
        "status_label": LINE_STATUS_LABELS[status],
        "shortfall_kg": shortfall,
    }


def check_ink_line(line: PrintJobInk, catalog: dict[str, dict]) -> dict:
    entry = catalog.get(f"ink:{line.ink_type_id}") if line.ink_type_id else None
    cans = entry["cans"] if entry else 0.0
    kg = entry["kg"] if entry else 0.0
    weight_per_can = entry["weight_per_can"] if entry else 0.0
    listed = entry is not None
    if listed and line.required_kg and weight_per_can <= 0:
        # KG unknown for this ink (no weight per can) — judge on cans only.
        status, shortfall = _line_status(cans, None, listed=True)
    else:
        status, shortfall = _line_status(kg if weight_per_can > 0 else cans, line.required_kg, listed=listed)
        if listed and weight_per_can <= 0:
            shortfall = 0.0
    return {
        "line": line,
        "line_number": line.line_number,
        "label": ink_label(line.ink_company, line.ink_name, line.shade),
        "company": line.ink_company or "",
        "name": line.ink_name,
        "shade": line.shade or "",
        "required_kg": line.required_kg,
        "cans": cans,
        "kg": kg,
        "weight_per_can": weight_per_can,
        "status": status,
        "status_label": LINE_STATUS_LABELS[status],
        "shortfall_kg": shortfall,
    }


def evaluate_job(job: PrintJob, material_catalog=None, ink_catalog=None) -> dict:
    """Live stock check for one job. Jobs plan materials only; inks are no longer part of a job."""
    material_catalog = material_catalog if material_catalog is not None else build_material_catalog()
    materials = [check_material_line(line, material_catalog) for line in job.materials]
    problems = [row for row in materials if row["status"] != STATUS_OK]
    missing = [row for row in materials if row["status"] in (STATUS_OUT, STATUS_UNLISTED)]
    short = [row for row in materials if row["status"] == STATUS_SHORT]
    return {
        "job": job,
        "materials": materials,
        "inks": [],
        "ready": not problems,
        "problem_count": len(problems),
        "not_in_stock": missing,
        "short": short,
        "materials_ok": not problems,
        "inks_ok": True,
    }


def evaluate_jobs(jobs: list[PrintJob]) -> list[dict]:
    material_catalog = build_material_catalog()
    return [evaluate_job(job, material_catalog) for job in jobs]


# --------------------------------------------------------------------------- queries


def list_jobs(status: str | None = None, search: str | None = None) -> list[PrintJob]:
    query = PrintJob.query
    if status == "open":
        query = query.filter(PrintJob.status.in_(PrintJob.OPEN_STATUSES))
    elif status in PrintJob.STATUSES:
        query = query.filter(PrintJob.status == status)
    if search:
        like = f"%{search.strip()}%"
        query = query.filter(
            db.or_(
                PrintJob.job_name.ilike(like),
                PrintJob.job_number.ilike(like),
                PrintJob.customer_name.ilike(like),
            )
        )
    return query.order_by(PrintJob.job_date.desc(), PrintJob.id.desc()).all()


def list_open_jobs() -> list[PrintJob]:
    return (
        PrintJob.query.filter(PrintJob.status.in_(PrintJob.OPEN_STATUSES))
        .order_by(PrintJob.job_date.asc(), PrintJob.id.asc())
        .all()
    )


def dashboard_stats() -> dict:
    open_jobs = evaluate_jobs(list_open_jobs())
    today = pkt_today()
    month_start = today.replace(day=1)
    counts = {
        status: PrintJob.query.filter(PrintJob.status == status).count()
        for status in PrintJob.STATUSES
    }
    completed_this_month = PrintJob.query.filter(
        PrintJob.status == PrintJob.STATUS_COMPLETED,
        PrintJob.job_date >= month_start,
    ).count()
    return {
        "today": today,
        "counts": counts,
        "total": sum(counts.values()),
        "open": open_jobs,
        "open_count": len(open_jobs),
        "ready_count": sum(1 for row in open_jobs if row["ready"]),
        "short_count": sum(1 for row in open_jobs if not row["ready"]),
        "completed_this_month": completed_this_month,
        "recent": PrintJob.query.order_by(PrintJob.created_at.desc()).limit(8).all(),
    }


# --------------------------------------------------------------------------- forms


def _parse_date(value: str) -> date:
    try:
        return datetime.strptime((value or "").strip(), "%Y-%m-%d").date()
    except ValueError as exc:
        raise ValueError("Enter a valid job date.") from exc


def _parse_optional_kg(value, label: str) -> float | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        amount = float(text)
    except ValueError as exc:
        raise ValueError(f"{label}: enter a number for KG.") from exc
    if amount < 0:
        raise ValueError(f"{label}: KG cannot be negative.")
    return amount or None


def _parse_production_qty(value) -> float:
    text = str(value or "").replace(",", "").strip()
    if not text:
        raise ValueError("Enter the total to produce for this job.")
    try:
        amount = float(text)
    except ValueError as exc:
        raise ValueError("Total to produce must be a number.") from exc
    if amount <= 0:
        raise ValueError("Total to produce must be more than zero.")
    return amount


def _parse_production_unit(value) -> str:
    unit = _clean(value)
    for allowed in PRODUCTION_UNITS:
        if unit.casefold() == allowed.casefold():
            return allowed
    raise ValueError("Pick a unit for the total to produce.")


def parse_job_form(form, material_catalog: dict, ink_catalog: dict | None = None) -> dict:
    job_name = _clean(form.get("job_name"))
    if not job_name:
        raise ValueError("Job name is required.")

    data = {
        "job_date": _parse_date(form.get("job_date")),
        "job_name": job_name,
        "customer_name": _clean(form.get("customer_name")) or None,
        "notes": (form.get("notes") or "").strip() or None,
        "production_qty": _parse_production_qty(form.get("production_qty")),
        "production_unit": _parse_production_unit(form.get("production_unit")),
        "materials": [],
        "inks": [],
    }

    for slot in MATERIAL_SLOTS:
        label = f"Material {slot}"
        choice = (form.get(f"material_{slot}") or "").strip()
        if not choice:
            if slot in REQUIRED_MATERIAL_SLOTS:
                raise ValueError(f"{label} is required.")
            continue

        entry = material_catalog.get(choice)
        if not entry:
            raise ValueError(
                f"{label}: pick a material from In Hall or Machine Hall stock."
            )
        category, name, size, micron = (
            entry["category"], entry["name"], entry["size"], entry["micron"]
        )

        data["materials"].append(
            {
                "position": slot,
                "material_key": material_key(name, size, micron),
                "category": category,
                "material_name": name,
                "size": size,
                "micron": micron,
                "required_kg": _parse_optional_kg(form.get(f"material_{slot}_kg"), label),
            }
        )

    choices = form.getlist("ink_choice")
    companies = form.getlist("ink_new_company")
    names = form.getlist("ink_new_name")
    shades = form.getlist("ink_new_shade")
    kgs = form.getlist("ink_kg")

    def at(values, index):
        return values[index] if index < len(values) else ""

    for index, choice in enumerate(choices):
        choice = (choice or "").strip()
        if not choice:
            continue
        label = f"Ink {len(data['inks']) + 1}"
        if choice == NEW_CHOICE:
            name = _clean(at(names, index))
            if not name:
                raise ValueError(f"{label}: type the new ink name.")
            ink = {
                "ink_type_id": None,
                "ink_company": _clean(at(companies, index)),
                "ink_name": name,
                "shade": _clean(at(shades, index)),
            }
        else:
            entry = (ink_catalog or {}).get(choice)
            if not entry:
                raise ValueError(f"{label}: that ink is no longer in the list. Pick it again.")
            ink = {
                "ink_type_id": entry["ink_type_id"],
                "ink_company": entry["company"],
                "ink_name": entry["name"],
                "shade": entry["shade"],
            }
        ink["required_kg"] = _parse_optional_kg(at(kgs, index), label)
        ink["line_number"] = len(data["inks"]) + 1
        data["inks"].append(ink)

    return data


def _next_job_number() -> str:
    highest = 0
    for (number,) in db.session.query(PrintJob.job_number).all():
        match = re.search(r"(\d+)$", number or "")
        if match:
            highest = max(highest, int(match.group(1)))
    return f"JOB-{highest + 1:04d}"


def _apply_lines(job: PrintJob, data: dict) -> None:
    job.materials.clear()
    job.inks.clear()
    db.session.flush()
    for row in data["materials"]:
        job.materials.append(PrintJobMaterial(**row))
    for row in data["inks"]:
        job.inks.append(PrintJobInk(**row))


def create_job(data: dict, user_id: int | None) -> PrintJob:
    job = PrintJob(
        job_number=_next_job_number(),
        job_date=data["job_date"],
        job_name=data["job_name"],
        customer_name=data["customer_name"],
        production_qty=data["production_qty"],
        production_unit=data["production_unit"],
        notes=data["notes"],
        status=PrintJob.STATUS_PENDING,
        created_by_id=user_id,
    )
    db.session.add(job)
    _apply_lines(job, data)
    db.session.flush()
    return job


def update_job(job: PrintJob, data: dict) -> PrintJob:
    job.job_date = data["job_date"]
    job.job_name = data["job_name"]
    job.customer_name = data["customer_name"]
    job.production_qty = data["production_qty"]
    job.production_unit = data["production_unit"]
    job.notes = data["notes"]
    _apply_lines(job, data)
    db.session.flush()
    return job


def set_job_status(job: PrintJob, status: str) -> PrintJob:
    if status not in PrintJob.STATUSES:
        raise ValueError("Unknown job status.")
    job.status = status
    job.completed_at = datetime.now(timezone.utc) if status == PrintJob.STATUS_COMPLETED else None
    db.session.flush()
    return job


def job_form_values(job: PrintJob | None, material_catalog: dict, ink_catalog: dict) -> dict:
    """Pre-fill values for the job form (edit page, or blank for new)."""
    if job is None:
        return {
            "job_date": pkt_today().isoformat(),
            "job_name": "",
            "customer_name": "",
            "production_qty": "",
            "production_unit": "KG",
            "notes": "",
            "materials": {slot: {"choice": "", "kg": ""} for slot in MATERIAL_SLOTS},
            "inks": [{"choice": "", "kg": ""}],
        }

    materials = {slot: {"choice": "", "kg": ""} for slot in MATERIAL_SLOTS}
    for line in job.materials:
        materials[line.position] = {
            "choice": line.material_key if line.material_key in material_catalog else "",
            "kg": _fmt_kg_input(line.required_kg),
        }

    inks = []
    for line in job.inks:
        key = f"ink:{line.ink_type_id}" if line.ink_type_id else manual_ink_key(
            line.ink_company, line.ink_name, line.shade
        )
        inks.append({"choice": key if key in ink_catalog else "", "kg": _fmt_kg_input(line.required_kg)})

    return {
        "job_date": job.job_date.isoformat(),
        "job_name": job.job_name,
        "customer_name": job.customer_name or "",
        "production_qty": _fmt_kg_input(job.production_qty),
        "production_unit": job.production_unit or "KG",
        "notes": job.notes or "",
        "materials": materials,
        "inks": inks or [{"choice": "", "kg": ""}],
    }


def _fmt_kg_input(value) -> str:
    if value is None:
        return ""
    return f"{float(value):.3f}".rstrip("0").rstrip(".")
