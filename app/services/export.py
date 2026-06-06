from io import BytesIO
from datetime import datetime

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer


def export_inventory_excel(rows, title="Inventory Report"):
    wb = Workbook()
    ws = wb.active
    ws.title = "Inventory"

    headers = [
        "Company",
        "Ink Type",
        "Opening Stock",
        "Total Received",
        "Total Used",
        "Current Stock",
        "Low Stock Threshold",
        "Status",
    ]

    header_fill = PatternFill(start_color="1F4E79", end_color="1F4E79", fill_type="solid")
    header_font = Font(color="FFFFFF", bold=True)

    ws.append(headers)
    for col in range(1, len(headers) + 1):
        cell = ws.cell(row=1, column=col)
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(horizontal="center")

    for item in rows:
        ws.append(
            [
                item["company"].name,
                item["ink_type"].name,
                item["opening"],
                item["received"],
                item["used"],
                item["current"],
                item["threshold"],
                "LOW" if item["is_low"] else "OK",
            ]
        )

    for col in ws.columns:
        max_length = 0
        column = col[0].column_letter
        for cell in col:
            if cell.value is not None:
                max_length = max(max_length, len(str(cell.value)))
        ws.column_dimensions[column].width = min(max_length + 2, 40)

    output = BytesIO()
    wb.save(output)
    output.seek(0)
    return output


def export_transactions_excel(transactions):
    wb = Workbook()
    ws = wb.active
    ws.title = "Transactions"

    headers = ["Date", "Company", "Ink Type", "Type", "Quantity", "Notes", "Entered By", "Created At"]
    ws.append(headers)

    for txn in transactions:
        ws.append(
            [
                txn.transaction_date.strftime("%Y-%m-%d"),
                txn.company.name,
                txn.ink_type.name,
                txn.transaction_type,
                txn.quantity,
                txn.notes or "",
                txn.created_by.username if txn.created_by else "",
                txn.created_at.strftime("%Y-%m-%d %H:%M"),
            ]
        )

    output = BytesIO()
    wb.save(output)
    output.seek(0)
    return output


def export_inventory_pdf(rows, title="Inventory Report"):
    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=landscape(A4), topMargin=0.5 * inch)
    styles = getSampleStyleSheet()
    elements = []

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
        [
            "Company",
            "Ink Type",
            "Opening",
            "Received",
            "Used",
            "Current",
            "Threshold",
            "Status",
        ]
    ]
    for item in rows:
        data.append(
            [
                item["company"].name,
                item["ink_type"].name,
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
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1F4E79")),
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
