"""PDF field definitions and branded report generation."""

from datetime import datetime
from io import BytesIO
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from app.module_context import MODULE_CHEMICALS, MODULE_GLUE, MODULE_INK, MODULE_MATERIALS
from app.services.glue_chemical_inventory import chemical_live_stock, glue_live_stock
from app.services.inventory import calculate_live_stock
from app.services.materials_inventory import calculate_live_stock as material_live_stock

LOGO_PATH = Path(__file__).resolve().parent.parent / "static" / "images" / "rn-colour-logo.png"
BRAND_RED = colors.HexColor("#B21E22")
BRAND_BLACK = colors.HexColor("#1A1A1A")
HEADER_BG = BRAND_RED


MODULE_PDF_FIELDS = {
    MODULE_INK: [
        ("company", "Company"),
        ("item", "Ink Name"),
        ("color_code", "Color Code"),
        ("unit_type", "Unit Type"),
        ("opening", "Opening"),
        ("received", "Received"),
        ("used", "Used"),
        ("current", "Current Stock"),
        ("threshold", "Threshold"),
        ("status", "Status"),
    ],
    MODULE_MATERIALS: [
        ("company", "Company"),
        ("category", "Category"),
        ("item", "Item Name"),
        ("size", "Size"),
        ("micron", "Micron"),
        ("opening", "Opening (kg)"),
        ("received", "Purchased (kg)"),
        ("used", "Used (kg)"),
        ("current", "Current (kg)"),
        ("threshold", "Threshold"),
        ("status", "Status"),
    ],
    MODULE_GLUE: [
        ("company", "Company"),
        ("item", "Item Name"),
        ("unit_type", "Unit Type"),
        ("opening", "Opening"),
        ("received", "Received"),
        ("used", "Used"),
        ("current", "Current Stock"),
        ("threshold", "Threshold"),
        ("status", "Status"),
    ],
    MODULE_CHEMICALS: [
        ("company", "Company"),
        ("item", "Item Name"),
        ("unit_type", "Unit Type"),
        ("opening", "Opening"),
        ("received", "Received"),
        ("used", "Used"),
        ("current", "Current Stock"),
        ("threshold", "Threshold"),
        ("status", "Status"),
    ],
}


def get_module_rows(module: str, company_id=None):
    if module == MODULE_INK:
        rows = calculate_live_stock(company_id=company_id)
        return [_normalize_ink_row(r) for r in rows]
    if module == MODULE_MATERIALS:
        rows = material_live_stock(company_id=company_id)
        return [_normalize_material_row(r) for r in rows]
    if module == MODULE_GLUE:
        rows = glue_live_stock(company_id=company_id)
        return [_normalize_product_row(r) for r in rows]
    if module == MODULE_CHEMICALS:
        rows = chemical_live_stock(company_id=company_id)
        return [_normalize_product_row(r) for r in rows]
    return []


def _normalize_ink_row(row):
    ink = row["ink_type"]
    return {
        "company": row["company"].name,
        "item": ink.name,
        "color_code": ink.color_code or "—",
        "unit_type": ink.unit_type or "—",
        "opening": f"{row['opening']:.1f}",
        "received": f"{row['received']:.1f}",
        "used": f"{row['used']:.1f}",
        "current": f"{row['current']:.1f}",
        "threshold": str(row["threshold"]),
        "status": "LOW" if row["is_low"] else "OK",
    }


def _normalize_material_row(row):
    material = row["material"]
    return {
        "company": row["company"].name,
        "category": material.category,
        "item": material.name,
        "size": material.size or "—",
        "micron": material.micron or "—",
        "opening": f"{row['opening']:.1f}",
        "received": f"{row['received']:.1f}",
        "used": f"{row['used']:.1f}",
        "current": f"{row['current']:.1f}",
        "threshold": str(row["threshold"]),
        "status": "LOW" if row["is_low"] else "OK",
    }


def _normalize_product_row(row):
    item = row["item"]
    return {
        "company": row["company"].name,
        "item": item.name,
        "unit_type": item.unit_type,
        "opening": f"{row['opening']:.1f}",
        "received": f"{row['received']:.1f}",
        "used": f"{row['used']:.1f}",
        "current": f"{row['current']:.1f}",
        "threshold": str(row["threshold"]),
        "status": "LOW" if row["is_low"] else "OK",
    }


def generate_custom_pdf(module: str, selected_fields: list[str], company_id=None, title=None):
    fields = MODULE_PDF_FIELDS.get(module, [])
    field_map = {key: label for key, label in fields}
    headers = [field_map[key] for key in selected_fields if key in field_map]
    rows = get_module_rows(module, company_id=company_id)

    data = [headers]
    for row in rows:
        data.append([row.get(key, "—") for key in selected_fields if key in field_map])

    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=landscape(A4), topMargin=0.45 * inch)
    styles = getSampleStyleSheet()
    elements = []

    if LOGO_PATH.exists():
        elements.append(Image(str(LOGO_PATH), width=2 * inch, height=0.78 * inch, kind="proportional"))
        elements.append(Spacer(1, 0.12 * inch))

    title_style = ParagraphStyle(
        "BrandTitle",
        parent=styles["Heading1"],
        fontSize=16,
        textColor=BRAND_BLACK,
        spaceAfter=6,
    )
    subtitle_style = ParagraphStyle(
        "BrandSubtitle",
        parent=styles["Normal"],
        fontSize=9,
        textColor=BRAND_RED,
    )
    report_title = title or f"RN COLOUR — {module.replace('_', ' ').title()} Report"
    elements.append(Paragraph(report_title, title_style))
    elements.append(
        Paragraph(
            f"Generated: {datetime.now().strftime('%d %B %Y %H:%M')}",
            subtitle_style,
        )
    )
    elements.append(Spacer(1, 0.18 * inch))

    table = Table(data, repeatRows=1)
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), HEADER_BG),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 8),
                ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#CCCCCC")),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#FDF5F5")]),
                ("TEXTCOLOR", (0, 1), (-1, -1), BRAND_BLACK),
            ]
        )
    )
    elements.append(table)
    doc.build(elements)
    buffer.seek(0)
    return buffer
