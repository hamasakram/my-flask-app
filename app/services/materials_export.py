from datetime import datetime

from io import BytesIO

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from app.services.export import BRAND_HEADER_COLOR, LOGO_PATH
from reportlab.platypus import Image


def export_materials_daily_pdf(report_data):
    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, topMargin=0.5 * inch, bottomMargin=0.5 * inch)
    styles = getSampleStyleSheet()
    elements = []

    if LOGO_PATH.exists():
        logo = Image(str(LOGO_PATH), width=2.2 * inch, height=0.85 * inch, kind="proportional")
        elements.append(logo)
        elements.append(Spacer(1, 0.15 * inch))

    title_style = ParagraphStyle("Title", parent=styles["Heading1"], fontSize=16, spaceAfter=8)
    report_date = report_data["report_date"]
    elements.append(Paragraph("Printing Materials — Daily Report", title_style))
    elements.append(
        Paragraph(
            f"Date: {report_date.strftime('%d %B %Y')} &nbsp;|&nbsp; Generated: {datetime.now().strftime('%d-%m-%Y %H:%M')}",
            styles["Normal"],
        )
    )
    elements.append(Spacer(1, 0.2 * inch))

    elements.append(Paragraph("<b>Current Stock — All Materials</b>", styles["Heading3"]))
    stock_data = [
        ["Category", "Material", "Size", "Micron", "Initial KG", "Stock In", "Used", "Left (KG)"]
    ]
    for item in report_data["live_stock"]:
        m = item["material"]
        stock_data.append(
            [
                m.category,
                m.name,
                m.size or "—",
                m.micron or "—",
                f"{item['initial_kg']:.1f}",
                f"{item['stock_in']:.1f}",
                f"{item['used']:.1f}",
                f"{item['current']:.1f}",
            ]
        )
    if len(stock_data) == 1:
        stock_data.append(["—", "No materials added yet", "—", "—", "—", "—", "—", "—"])

    stock_table = Table(stock_data, repeatRows=1, colWidths=[55, 80, 55, 45, 50, 50, 45, 50])
    stock_table.setStyle(
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
    elements.append(stock_table)
    elements.append(Spacer(1, 0.25 * inch))

    elements.append(
        Paragraph(
            f"<b>Today's Activity</b> — Stock In: {report_data['total_stock_in']:.1f} kg &nbsp;|&nbsp; Used: {report_data['total_used']:.1f} kg",
            styles["Heading3"],
        )
    )
    elements.append(Spacer(1, 0.1 * inch))

    activity_data = [
        ["Type", "Material", "Qty (KG)", "Left (KG)", "Where Used", "Notes"]
    ]
    for txn in report_data["stock_in_today"]:
        activity_data.append(
            [
                "Stock In",
                txn.material.display_name,
                f"{txn.quantity:.1f}",
                "—",
                "—",
                txn.notes or "—",
            ]
        )
    for txn in report_data["stock_left_today"]:
        activity_data.append(
            [
                "Stock Left",
                txn.material.display_name,
                f"{txn.quantity:.1f}",
                f"{txn.quantity_left:.1f}" if txn.quantity_left is not None else "—",
                txn.where_used or "—",
                txn.notes or "—",
            ]
        )
    if len(activity_data) == 1:
        activity_data.append(["—", "No activity today", "—", "—", "—", "—"])

    activity_table = Table(activity_data, repeatRows=1)
    activity_table.setStyle(
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
    elements.append(activity_table)

    doc.build(elements)
    buffer.seek(0)
    return buffer
