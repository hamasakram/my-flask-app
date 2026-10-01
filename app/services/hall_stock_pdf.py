"""Printable stock lists for In Hall and Machine Hall."""

from datetime import datetime
from io import BytesIO

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_RIGHT
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from app.models import HallStockItem, HallStockMovement


def _format_num(value, digits: int = 3) -> str:
    if value is None:
        return "—"
    amount = float(value)
    if amount == 0:
        return "0"
    return f"{amount:,.{digits}f}"


def generate_hall_stock_pdf(
    title: str,
    items: list[HallStockItem],
    movements: list[HallStockMovement],
    movement_title: str,
    *,
    include_where_used: bool = False,
) -> BytesIO:
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=landscape(A4),
        topMargin=0.4 * inch,
        bottomMargin=0.4 * inch,
        leftMargin=0.4 * inch,
        rightMargin=0.4 * inch,
    )
    styles = getSampleStyleSheet()
    brand = colors.HexColor("#1A1A1A")
    header_bg = colors.HexColor("#B21E22")
    alt = colors.HexColor("#F3F4F6")
    border = colors.HexColor("#9CA3AF")
    muted = colors.HexColor("#4B5563")

    title_style = ParagraphStyle(
        "HallTitle",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=16,
        alignment=TA_CENTER,
        textColor=brand,
        spaceAfter=2,
    )
    sub_style = ParagraphStyle(
        "HallSub",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=9,
        alignment=TA_CENTER,
        textColor=muted,
        spaceAfter=10,
    )
    section_style = ParagraphStyle(
        "HallSection",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=11,
        textColor=brand,
        spaceBefore=8,
        spaceAfter=6,
    )
    cell = ParagraphStyle(
        "HallCell",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=8,
        leading=10,
        textColor=brand,
    )
    cell_right = ParagraphStyle("HallRight", parent=cell, alignment=TA_RIGHT)
    head = ParagraphStyle(
        "HallHead",
        parent=cell,
        fontName="Helvetica-Bold",
        textColor=colors.white,
        alignment=TA_CENTER,
    )

    total_rolls = sum(float(item.rolls_left or 0) for item in items)
    total_kg = sum(float(item.kg or 0) for item in items)

    elements = [
        Paragraph(title, title_style),
        Paragraph(
            f"Printed {datetime.now().strftime('%A %d/%m/%Y %H:%M')} · "
            f"{len(items)} material(s) · Rolls {total_rolls:,.2f} · KG {total_kg:,.3f}",
            sub_style,
        ),
        Paragraph("Current Stock", section_style),
    ]

    stock_header = [
        Paragraph("Material Name", head),
        Paragraph("Type", head),
        Paragraph("Size", head),
        Paragraph("Micron", head),
        Paragraph("Rolls Left", head),
        Paragraph("KGs", head),
    ]
    stock_data = [stock_header]
    if items:
        for item in items:
            stock_data.append(
                [
                    Paragraph(item.material_name, cell),
                    Paragraph(item.material_type or "—", cell),
                    Paragraph(item.size or "—", cell),
                    Paragraph(item.micron or "—", cell),
                    Paragraph(_format_num(item.rolls_left, 2), cell_right),
                    Paragraph(_format_num(item.kg, 3), cell_right),
                ]
            )
        stock_data.append(
            [
                Paragraph("Total", ParagraphStyle("Tot", parent=cell, fontName="Helvetica-Bold")),
                "",
                "",
                "",
                Paragraph(_format_num(total_rolls, 2), ParagraphStyle("TotR", parent=cell_right, fontName="Helvetica-Bold")),
                Paragraph(_format_num(total_kg, 3), ParagraphStyle("TotK", parent=cell_right, fontName="Helvetica-Bold")),
            ]
        )
    else:
        stock_data.append([Paragraph("No stock listed.", cell), "", "", "", "", ""])

    stock_table = Table(
        stock_data,
        colWidths=[2.6 * inch, 1.6 * inch, 1.5 * inch, 1.1 * inch, 1.3 * inch, 1.4 * inch],
        repeatRows=1,
    )
    stock_style = [
        ("BACKGROUND", (0, 0), (-1, 0), header_bg),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("BOX", (0, 0), (-1, -1), 0.6, border),
        ("INNERGRID", (0, 0), (-1, -1), 0.3, border),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN", (4, 1), (-1, -1), "RIGHT"),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("ROWBACKGROUNDS", (0, 1), (-1, -2 if items else -1), [colors.white, alt]),
    ]
    if items:
        stock_style.append(("BACKGROUND", (0, -1), (-1, -1), colors.HexColor("#E5E7EB")))
    stock_table.setStyle(TableStyle(stock_style))
    elements.append(stock_table)

    elements.append(Paragraph(movement_title, section_style))
    move_header = [
        Paragraph("Date", head),
        Paragraph("Material", head),
        Paragraph("Type", head),
        Paragraph("Size", head),
        Paragraph("Micron", head),
        Paragraph("KG", head),
        Paragraph("Rolls", head),
    ]
    if include_where_used:
        move_header.append(Paragraph("Where Used", head))
    move_header.append(Paragraph("Notes", head))

    move_data = [move_header]
    if movements:
        for row in movements:
            line = [
                Paragraph(row.movement_date.strftime("%d/%m/%Y"), cell),
                Paragraph(row.material_name, cell),
                Paragraph(row.material_type or "—", cell),
                Paragraph(row.size or "—", cell),
                Paragraph(row.micron or "—", cell),
                Paragraph(_format_num(row.gross_kg, 3), cell_right),
                Paragraph(_format_num(row.rolls, 2), cell_right),
            ]
            if include_where_used:
                line.append(Paragraph(row.where_used or "—", cell))
            line.append(Paragraph(row.notes or "—", cell))
            move_data.append(line)
    else:
        move_data.append([Paragraph("No records yet.", cell)] + [""] * (len(move_header) - 1))

    if include_where_used:
        widths = [0.9 * inch, 1.7 * inch, 1.1 * inch, 0.9 * inch, 0.7 * inch, 0.9 * inch, 0.7 * inch, 1.8 * inch, 1.5 * inch]
    else:
        widths = [1.0 * inch, 2.0 * inch, 1.3 * inch, 1.1 * inch, 0.9 * inch, 1.0 * inch, 0.9 * inch, 2.0 * inch]

    move_table = Table(move_data, colWidths=widths, repeatRows=1)
    move_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), header_bg),
                ("BOX", (0, 0), (-1, -1), 0.6, border),
                ("INNERGRID", (0, 0), (-1, -1), 0.3, border),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, alt]),
            ]
        )
    )
    elements.append(move_table)
    elements.append(Spacer(1, 10))
    elements.append(
        Paragraph(
            "RN COLOUR · Printing Materials · This report is separate from Stock In and Stock Left.",
            ParagraphStyle(
                "HallFoot",
                parent=styles["Normal"],
                fontSize=8,
                alignment=TA_CENTER,
                textColor=muted,
            ),
        )
    )
    doc.build(elements)
    buffer.seek(0)
    return buffer
