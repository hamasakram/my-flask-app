from datetime import datetime
from io import BytesIO

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from app.services.export import BRAND_HEADER_COLOR, LOGO_PATH


def _table_style(header_rows=1):
    return TableStyle(
        [
            ("BACKGROUND", (0, 0), (-1, header_rows - 1), colors.HexColor(f"#{BRAND_HEADER_COLOR}")),
            ("TEXTCOLOR", (0, 0), (-1, header_rows - 1), colors.white),
            ("FONTNAME", (0, 0), (-1, header_rows - 1), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 8),
            ("ALIGN", (0, 0), (-1, -1), "CENTER"),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
            ("ROWBACKGROUNDS", (0, header_rows), (-1, -1), [colors.white, colors.HexColor("#F2F6FA")]),
            ("TOPPADDING", (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ]
    )


def export_materials_daily_pdf(report_data):
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=landscape(A4),
        topMargin=0.45 * inch,
        bottomMargin=0.45 * inch,
        leftMargin=0.4 * inch,
        rightMargin=0.4 * inch,
    )
    styles = getSampleStyleSheet()
    elements = []

    category_title = ParagraphStyle(
        "CategoryTitle",
        parent=styles["Heading2"],
        fontSize=13,
        textColor=colors.HexColor(f"#{BRAND_HEADER_COLOR}"),
        spaceBefore=10,
        spaceAfter=6,
    )
    section_label = ParagraphStyle(
        "SectionLabel",
        parent=styles["Normal"],
        fontSize=9,
        textColor=colors.HexColor("#475569"),
        spaceAfter=4,
    )
    title_style = ParagraphStyle("Title", parent=styles["Heading1"], fontSize=16, spaceAfter=6)

    if LOGO_PATH.exists():
        logo = Image(str(LOGO_PATH), width=2.0 * inch, height=0.75 * inch, kind="proportional")
        elements.append(logo)
        elements.append(Spacer(1, 0.12 * inch))

    report_date = report_data["report_date"]
    elements.append(Paragraph("Printing Materials — Daily Report", title_style))
    elements.append(
        Paragraph(
            f"<b>Date:</b> {report_date.strftime('%d %B %Y')} &nbsp;&nbsp; "
            f"<b>Generated:</b> {datetime.now().strftime('%d-%m-%Y %H:%M')}",
            styles["Normal"],
        )
    )
    elements.append(
        Paragraph(
            f"<b>Total Stock In Today:</b> {report_data['total_stock_in']:.1f} kg &nbsp;&nbsp; "
            f"<b>Total Used Today:</b> {report_data['total_used']:.1f} kg",
            styles["Normal"],
        )
    )
    elements.append(Spacer(1, 0.2 * inch))

    grouped = report_data.get("grouped_categories") or []
    if not grouped:
        elements.append(Paragraph("No materials recorded yet.", styles["Normal"]))
    else:
        stock_col_widths = [80, 55, 45, 55, 55, 55, 55]
        activity_col_widths = [55, 80, 55, 55, 55, 95, 95]

        for idx, category in enumerate(grouped):
            if idx > 0:
                elements.append(Spacer(1, 0.15 * inch))

            elements.append(Paragraph(f"CATEGORY: {category['name'].upper()}", category_title))
            elements.append(
                Paragraph(
                    f"Category Total Left: <b>{category['total_left']:.1f} kg</b> &nbsp;|&nbsp; "
                    f"Stock In Today: <b>{category['stock_in_today']:.1f} kg</b> &nbsp;|&nbsp; "
                    f"Used Today: <b>{category['used_today']:.1f} kg</b>",
                    section_label,
                )
            )

            stock_data = [
                ["Material", "Size", "Micron", "Initial KG", "Stock In", "Used", "Left (KG)"]
            ]
            for material_block in category["materials"]:
                material = material_block["material"]
                stock = material_block["stock"]
                stock_data.append(
                    [
                        material.name,
                        material.size or "—",
                        material.micron or "—",
                        f"{stock['initial_kg']:.1f}",
                        f"{stock['stock_in']:.1f}",
                        f"{stock['used']:.1f}",
                        f"{stock['current']:.1f}",
                    ]
                )

            stock_table = Table(stock_data, colWidths=stock_col_widths, repeatRows=1)
            stock_table.setStyle(_table_style())
            elements.append(stock_table)
            elements.append(Spacer(1, 0.1 * inch))

            activity_rows = []
            for material_block in category["materials"]:
                material = material_block["material"]
                size_label = material.size or "—"
                for txn in material_block["stock_in_today"]:
                    activity_rows.append(
                        [
                            "Stock In",
                            material.name,
                            size_label,
                            f"{txn.quantity:.1f}",
                            "—",
                            "—",
                            txn.notes or "—",
                        ]
                    )
                for txn in material_block["stock_left_today"]:
                    activity_rows.append(
                        [
                            "Stock Left",
                            material.name,
                            size_label,
                            f"{txn.quantity:.1f}",
                            f"{txn.quantity_left:.1f}" if txn.quantity_left is not None else "—",
                            txn.where_used or "—",
                            txn.notes or "—",
                        ]
                    )

            if activity_rows:
                elements.append(Paragraph("<b>Today's Activity</b>", section_label))
                activity_data = [
                    ["Type", "Material", "Size", "Qty (KG)", "Left (KG)", "Where Used", "Notes"]
                ] + activity_rows
                activity_table = Table(activity_data, colWidths=activity_col_widths, repeatRows=1)
                activity_table.setStyle(_table_style())
                elements.append(activity_table)

            elements.append(Spacer(1, 0.12 * inch))

    doc.build(elements)
    buffer.seek(0)
    return buffer
