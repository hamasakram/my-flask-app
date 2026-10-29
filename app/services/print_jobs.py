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

    # Materials typed on earlier jobs (not in either hall) stay selectable as "to purchase".
    for line in PrintJobMaterial.query.all():
        if line.material_key not in entries:
            entries[line.material_key] = _blank_material_entry(
                line.material_key, line.category, line.material_name, line.size, line.micron
            )

    for entry in entries.values():
        entry["total_kg"] = entry["in_hall_kg"] + entry["machine_kg"]
        entry["in_stock"] = entry["total_kg"] > EPSILON
        entry["label"] = material_label(
            entry["category"], entry["name"], entry["size"], entry["micron"]
        )
    return entries


def material_options(catalog: dict[str, dict]) -> list[dict]:
    """Dropdown groups: in-stock hall materials by category, zero-balance hall items,
    then materials typed on earlier jobs that are not in either hall (to purchase)."""
    in_stock: dict[str, list] = {}
    zero: list = []
    to_purchase: list = []
    for entry in sorted(catalog.values(), key=lambda e: e["label"].casefold()):
        if entry["in_stock"]:
            group = entry["category"] or "Other"
            in_stock.setdefault(group, []).append(entry)
        elif entry["listed"]:
            zero.append(entry)
        else:
            to_purchase.append(entry)
    groups = [
        {"label": f"In Stock — {name}", "options": in_stock[name]}
        for name in sorted(in_stock, key=str.casefold)
    ]
    if zero:
        groups.append({"label": "Not in Stock — 0 kg in In Hall / Machine Hall", "options": zero})
    if to_purchase:
        groups.append({"label": "Not in Stock — required to purchase (added on earlier jobs)", "options": to_purchase})
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
    was = getattr(line, "status_at_save", None)
    became_available = status == STATUS_OK and was in (STATUS_OUT, STATUS_UNLISTED, STATUS_SHORT)
    return {
        "line": line,
        "position": line.position,
        "status_at_save": was,
        "became_available": became_available,
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
        "now_available": [row for row in materials if row["became_available"]],
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


# --------------------------------------------------------------------------- purchase list


def build_purchase_list(evaluations: list[dict]) -> dict:
    """Materials to buy for open jobs, combined across jobs.

    A material is listed when it has 0 kg in In Hall + Machine Hall (not in stock), or when the
    combined Required KG of all open jobs is more than what both halls hold (short).
    """
    items: dict[str, dict] = {}
    for evaluation in evaluations:
        job = evaluation["job"]
        for row in evaluation["materials"]:
            key = row["line"].material_key
            item = items.get(key)
            if item is None:
                item = items[key] = {
                    "key": key,
                    "label": row["label"],
                    "category": row["category"],
                    "name": row["name"],
                    "size": row["size"],
                    "micron": row["micron"],
                    "in_hall_kg": row["in_hall_kg"],
                    "machine_kg": row["machine_kg"],
                    "available_kg": row["total_kg"],
                    "in_hall_list": row["status"] != STATUS_UNLISTED,
                    "required_kg": 0.0,
                    "qty_missing": False,
                    "jobs": [],
                }
            if row["required_kg"]:
                item["required_kg"] += float(row["required_kg"])
            else:
                item["qty_missing"] = True
            item["jobs"].append(
                {"job": job, "required_kg": row["required_kg"], "status": row["status"]}
            )

    result = []
    for item in items.values():
        to_buy = max(0.0, item["required_kg"] - item["available_kg"])
        not_in_stock = item["available_kg"] <= EPSILON
        if not (not_in_stock or to_buy > EPSILON):
            continue
        item["to_buy_kg"] = to_buy
        item["reason"] = STATUS_OUT if not_in_stock else STATUS_SHORT
        item["reason_label"] = (
            "Not in Stock — required to purchase" if not_in_stock else "Short — buy the difference"
        )
        item["jobs"].sort(key=lambda j: (j["job"].job_date, j["job"].id))
        result.append(item)

    result.sort(key=lambda i: (0 if i["reason"] == STATUS_OUT else 1, i["label"].casefold()))
    return {
        "items": result,
        "not_in_stock_count": sum(1 for i in result if i["reason"] == STATUS_OUT),
        "short_count": sum(1 for i in result if i["reason"] == STATUS_SHORT),
        "total_to_buy_kg": sum(i["to_buy_kg"] for i in result),
        "qty_missing_count": sum(1 for i in result if i["qty_missing"]),
        "open_job_count": len(evaluations),
    }


def purchase_list_for_open_jobs() -> dict:
    return build_purchase_list(evaluate_jobs(list_open_jobs()))


# --------------------------------------------------------------------------- hall stock hooks


def open_jobs_using(name: str, size: str = "", micron: str = "") -> list[dict]:
    """Open jobs that need this material, with their live stock check (oldest first)."""
    key = material_key(name, size, micron)
    jobs = [
        job for job in list_open_jobs()
        if any(line.material_key == key for line in job.materials)
    ]
    return evaluate_jobs(jobs)


def hall_stock_job_notice(name: str, size: str = "", micron: str = "") -> str | None:
    """Message shown after hall stock is added: which open jobs need it and are they ready now."""
    rows = open_jobs_using(name, size, micron)
    if not rows:
        return None
    key = material_key(name, size, micron)
    parts = []
    for row in rows:
        job = row["job"]
        line = next(m for m in row["materials"] if m["line"].material_key == key)
        if row["ready"]:
            state = "now READY to run"
        elif line["status"] == STATUS_OK:
            state = f"this material is now in stock; {row['problem_count']} other material(s) still needed"
        elif line["status"] == STATUS_SHORT:
            state = f"still short {line['shortfall_kg']:,.2f} kg of this material"
        else:
            state = "still not in stock"
        parts.append(f"{job.job_number} {job.job_name} ({state})")
    total = rows[0]["materials"] and next(
        m for m in rows[0]["materials"] if m["line"].material_key == key
    )["total_kg"]
    return (
        f"Job Planning: {_clean(name)} is needed by {len(rows)} open job(s) — "
        f"{total:,.2f} kg now in In Hall + Machine Hall. " + "; ".join(parts) + "."
    )


def backfill_status_snapshots() -> None:
    """One-time: jobs saved before status_at_save existed get today's status as their baseline."""
    from app.models import AppSetting

    flag = "print_job_status_snapshot_v1"
    if AppSetting.query.filter_by(key=flag).first():
        return
    lines = PrintJobMaterial.query.filter(PrintJobMaterial.status_at_save.is_(None)).all()
    if lines:
        catalog = build_material_catalog()
        for line in lines:
            line.status_at_save = check_material_line(line, catalog)["status"]
    db.session.add(AppSetting(key=flag, value=f"done_{len(lines)}"))
    db.session.commit()


def hall_material_suggestions() -> list[dict]:
    """Materials open jobs are waiting for (not in stock or short), for the hall stock forms."""
    seen: dict[str, dict] = {}
    for row in evaluate_jobs(list_open_jobs()):
        for m in row["materials"]:
            if m["status"] == STATUS_OK:
                continue
            key = m["line"].material_key
            item = seen.setdefault(
                key,
                {
                    "name": m["name"],
                    "material_type": m["category"],
                    "size": m["size"],
                    "micron": m["micron"],
                    "label": m["label"],
                    "status": m["status"],
                    "jobs": [],
                },
            )
            item["jobs"].append(row["job"].job_number)
    return sorted(seen.values(), key=lambda i: i["label"].casefold())


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

        if choice == NEW_CHOICE:
            # Not in the hall lists: saved on the job and flagged Not in Stock / to purchase.
            name = _clean(form.get(f"material_{slot}_name"))
            if not name:
                raise ValueError(f"{label}: type the name of the material to purchase.")
            category = _clean(form.get(f"material_{slot}_category"))
            size = _clean(form.get(f"material_{slot}_size"))
            micron = _clean(form.get(f"material_{slot}_micron"))
        else:
            entry = material_catalog.get(choice)
            if not entry:
                raise ValueError(f"{label}: that material is no longer in the list. Pick it again.")
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
    # Keep the "status when saved" of materials that stay on the job, so editing a job
    # doesn't hide that a material has since arrived. New materials get today's status.
    previous = {line.material_key: line.status_at_save for line in job.materials}
    catalog = build_material_catalog()
    job.materials.clear()
    job.inks.clear()
    db.session.flush()
    for row in data["materials"]:
        line = PrintJobMaterial(**row)
        if row["material_key"] in previous and previous[row["material_key"]]:
            line.status_at_save = previous[row["material_key"]]
        else:
            line.status_at_save = check_material_line(line, catalog)["status"]
        job.materials.append(line)
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
