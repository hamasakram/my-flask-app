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
            ("FONTSIZE", (0, 0), (-1, -1), 7),
            ("ALIGN", (0, 0), (-1, -1), "CENTER"),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
            ("ROWBACKGROUNDS", (0, header_rows), (-1, -1), [colors.white, colors.HexColor("#F2F6FA")]),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ]
    )


def export_ink_daily_pdf(report_data):
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=landscape(A4),
        topMargin=0.45 * inch,
        bottomMargin=0.45 * inch,
        leftMargin=0.35 * inch,
        rightMargin=0.35 * inch,
    )
    styles = getSampleStyleSheet()
    elements = []

    company_title = ParagraphStyle(
        "CompanyTitle",
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
    elements.append(Paragraph("Ink Management — Daily Report", title_style))
    elements.append(
        Paragraph(
            f"<b>Date:</b> {report_date.strftime('%d %B %Y')} &nbsp;&nbsp; "
            f"<b>Generated:</b> {datetime.now().strftime('%d-%m-%Y %H:%M')}",
            styles["Normal"],
        )
    )
    elements.append(
        Paragraph(
            f"<b>Total Cans In Today:</b> {report_data['total_cans_in']:.1f} &nbsp;&nbsp; "
            f"<b>Total Cans Used Today:</b> {report_data['total_used']:.1f}",
            styles["Normal"],
        )
    )
    elements.append(Spacer(1, 0.2 * inch))

    grouped = report_data.get("grouped_companies") or []
    if not grouped:
        elements.append(Paragraph("No inks recorded yet.", styles["Normal"]))
    else:
        stock_col_widths = [50, 65, 45, 45, 50, 45, 45, 45, 50]
        activity_col_widths = [50, 65, 45, 45, 45, 70, 70]

        for idx, company in enumerate(grouped):
            if idx > 0:
                elements.append(Spacer(1, 0.12 * inch))

            elements.append(Paragraph(f"INK COMPANY: {company['name'].upper()}", company_title))
            elements.append(
                Paragraph(
                    f"Cans Left: <b>{company['total_cans_left']:.1f}</b> &nbsp;|&nbsp; "
                    f"Weight Left: <b>{company['total_weight_left']:.1f} kg</b> &nbsp;|&nbsp; "
                    f"Cans In Today: <b>{company['cans_in_today']:.1f}</b> &nbsp;|&nbsp; "
                    f"Used Today: <b>{company['used_today']:.1f}</b>",
                    section_label,
                )
            )

            stock_data = [
                [
                    "Color Code",
                    "Color",
                    "Init. Cans",
                    "Wt/Can",
                    "Init. Wt",
                    "Cans In",
                    "Used",
                    "Cans Left",
                    "Left Wt",
                ]
            ]
            for ink_block in company["inks"]:
                ink = ink_block["ink"]
                stock = ink_block["stock"]
                stock_data.append(
                    [
                        ink.color_code or "—",
                        ink.name,
                        f"{stock['initial_cans']:.1f}",
                        f"{ink.weight_per_can:.1f}",
                        f"{stock['initial_weight']:.1f}",
                        f"{stock['cans_in']:.1f}",
                        f"{stock['used']:.1f}",
                        f"{stock['current_cans']:.1f}",
                        f"{stock['current_weight']:.1f}",
                    ]
                )

            stock_table = Table(stock_data, colWidths=stock_col_widths, repeatRows=1)
            stock_table.setStyle(_table_style())
            elements.append(stock_table)
            elements.append(Spacer(1, 0.08 * inch))

            activity_rows = []
            for ink_block in company["inks"]:
                ink = ink_block["ink"]
                for txn in ink_block["cans_in_today"]:
                    activity_rows.append(
                        [
                            "Cans In",
                            ink.name,
                            ink.color_code or "—",
                            f"{txn.quantity:.1f}",
                            "—",
                            "—",
                            txn.notes or "—",
                        ]
                    )
                for txn in ink_block["cans_left_today"]:
                    activity_rows.append(
                        [
                            "Cans Left",
                            ink.name,
                            ink.color_code or "—",
                            f"{txn.quantity:.1f}",
                            f"{txn.quantity_left:.1f}" if txn.quantity_left is not None else "—",
                            txn.where_used or "—",
                            txn.notes or "—",
                        ]
                    )

            if activity_rows:
                elements.append(Paragraph("<b>Today's Activity</b>", section_label))
                activity_data = [
                    ["Type", "Color", "Code", "Qty (Cans)", "Left (Cans)", "Where Used", "Notes"]
                ] + activity_rows
                activity_table = Table(activity_data, colWidths=activity_col_widths, repeatRows=1)
                activity_table.setStyle(_table_style())
                elements.append(activity_table)

            elements.append(Spacer(1, 0.1 * inch))

    doc.build(elements)
    buffer.seek(0)
    return buffer
