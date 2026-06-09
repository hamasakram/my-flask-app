from io import BytesIO
from datetime import datetime

from app.services.export import (
    BRAND_HEADER_COLOR,
    LOGO_PATH,
    _add_excel_logo,
    _write_excel_headers,
)
from openpyxl import Workbook
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle


def export_material_inventory_excel(rows, title="Materials Inventory Report"):
    wb = Workbook()
    ws = wb.active
    ws.title = "Materials"

    headers = [
        "Company",
        "Material",
        "Opening (kg)",
        "Received (kg)",
        "Used (kg)",
        "Current (kg)",
        "Low Stock Threshold",
        "Status",
    ]

    header_row = _add_excel_logo(ws)
    next_row = _write_excel_headers(ws, headers, header_row)

    for offset, item in enumerate(rows):
        row_num = next_row + offset
        values = [
            item["company"].name,
            item["material"].display_name,
            item["opening"],
            item["received"],
            item["used"],
            item["current"],
            item["threshold"],
            "LOW" if item["is_low"] else "OK",
        ]
        for col, value in enumerate(values, start=1):
            ws.cell(row=row_num, column=col, value=value)

    output = BytesIO()
    wb.save(output)
    output.seek(0)
    return output


def export_material_transactions_excel(transactions):
    wb = Workbook()
    ws = wb.active
    ws.title = "Transactions"

    headers = [
        "Date",
        "Company",
        "Material",
        "Type",
        "Used (kg)",
        "Left (kg)",
        "Production / Notes",
        "Entered By",
        "Created At",
    ]
    header_row = _add_excel_logo(ws)
    next_row = _write_excel_headers(ws, headers, header_row)

    for offset, txn in enumerate(transactions):
        row_num = next_row + offset
        values = [
            txn.transaction_date.strftime("%Y-%m-%d"),
            txn.company.name,
            txn.material.display_name,
            txn.transaction_type,
            txn.quantity if txn.transaction_type == "Stock Used" else "",
            txn.quantity_left if txn.transaction_type == "Stock Used" else txn.quantity,
            txn.notes or "",
            txn.created_by.username if txn.created_by else "",
            txn.created_at.strftime("%Y-%m-%d %H:%M"),
        ]
        for col, value in enumerate(values, start=1):
            ws.cell(row=row_num, column=col, value=value)

    output = BytesIO()
    wb.save(output)
    output.seek(0)
    return output


def export_material_inventory_pdf(rows, title="Materials Inventory Report"):
    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=landscape(A4), topMargin=0.5 * inch)
    styles = getSampleStyleSheet()
    elements = []

    if LOGO_PATH.exists():
        logo = Image(str(LOGO_PATH), width=2.2 * inch, height=0.85 * inch, kind="proportional")
        elements.append(logo)
        elements.append(Spacer(1, 0.15 * inch))

    title_style = ParagraphStyle(
        "Title",
        parent=styles["Heading1"],
        fontSize=16,
        spaceAfter=12,
    )
    elements.append(Paragraph(title, title_style))
    elements.append(
        Paragraph(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}", styles["Normal"])
    )
    elements.append(Spacer(1, 0.2 * inch))

    data = [
        ["Company", "Material", "Opening", "Received", "Used", "Current", "Threshold", "Status"]
    ]
    for item in rows:
        data.append(
            [
                item["company"].name,
                item["material"].display_name,
                f"{item['opening']:.1f}",
                f"{item['received']:.1f}",
                f"{item['used']:.1f}",
                f"{item['current']:.1f}",
                str(item["threshold"]),
                "LOW" if item["is_low"] else "OK",
            ]
        )

    table = Table(data, repeatRows=1)
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(f"#{BRAND_HEADER_COLOR}")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 8),
                ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F2F6FA")]),
            ]
        )
    )
    elements.append(table)
    doc.build(elements)
    buffer.seek(0)
    return buffer
