"""Printable stock lists for In Hall and Machine Hall."""

from datetime import datetime
from io import BytesIO

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_RIGHT
from reportlab.lib.pagesizes import A4
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


def _col_width(labels: list[str], *, minimum: float, maximum: float, font_size: float = 8) -> float:
    longest = max((len(str(label or "")) for label in labels), default=1)
    width = (longest * font_size * 0.5 + 14) / 72 * inch
    return max(minimum, min(maximum, width))


def _group_by_type(rows, type_getter):
    groups: dict[str, dict] = {}
    for row in rows:
        label = (type_getter(row) or "").strip() or "Other"
        key = label.casefold()
        bucket = groups.setdefault(key, {"label": label, "rows": []})
        bucket["rows"].append(row)
    return [groups[key] for key in sorted(groups, key=lambda item: groups[item]["label"].casefold())]


def _styled_table(data, widths, *, has_total: bool = False, right_cols: tuple[int, ...] = (-2, -1)) -> Table:
    header_bg = colors.HexColor("#B21E22")
    alt = colors.HexColor("#F3F4F6")
    border = colors.HexColor("#D1D5DB")
    table = Table(data, colWidths=widths, hAlign="LEFT")
    style = [
        ("BACKGROUND", (0, 0), (-1, 0), header_bg),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("BOX", (0, 0), (-1, -1), 0.4, border),
        ("INNERGRID", (0, 0), (-1, -1), 0.25, border),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        *[("ALIGN", (col, 1), (col, -1), "RIGHT") for col in right_cols],
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
    ]
    last_data = -2 if has_total else -1
    if len(data) > 2 or (len(data) > 1 and not has_total):
        style.append(("ROWBACKGROUNDS", (0, 1), (-1, last_data), [colors.white, alt]))
    if has_total:
        style.append(("BACKGROUND", (0, -1), (-1, -1), colors.HexColor("#E5E7EB")))
        style.append(("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"))
    table.setStyle(TableStyle(style))
    return table


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
        pagesize=A4,
        topMargin=0.45 * inch,
        bottomMargin=0.45 * inch,
        leftMargin=0.5 * inch,
        rightMargin=0.5 * inch,
    )
    styles = getSampleStyleSheet()
    brand = colors.HexColor("#1A1A1A")
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
        spaceAfter=12,
    )
    section_style = ParagraphStyle(
        "HallSection",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=12,
        textColor=brand,
        spaceBefore=10,
        spaceAfter=6,
    )
    type_style = ParagraphStyle(
        "HallType",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=10,
        textColor=colors.white,
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
        fontSize=8,
    )
    bold = ParagraphStyle("HallBold", parent=cell, fontName="Helvetica-Bold")
    bold_right = ParagraphStyle("HallBoldRight", parent=cell_right, fontName="Helvetica-Bold")

    total_rolls = sum(float(item.rolls_left or 0) for item in items)
    total_kg = sum(float(item.kg or 0) for item in items)

    elements = [
        Paragraph(title, title_style),
        Paragraph(
            f"Printed {datetime.now().strftime('%d/%m/%Y %H:%M')} · "
            f"{len(items)} material(s) · Rolls {total_rolls:,.2f} · KG {total_kg:,.3f}",
            sub_style,
        ),
        Paragraph("Current Stock", section_style),
    ]

    stock_headers = ["Material", "Size", "Micron", "Rolls", "KG"]

    def add_type_heading(label: str, width: float):
        bar = Table(
            [[Paragraph(label, type_style)]],
            colWidths=[width],
            hAlign="LEFT",
        )
        bar.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#1A1A1A")),
                    ("TOPPADDING", (0, 0), (-1, -1), 4),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                    ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ]
            )
        )
        elements.append(Spacer(1, 8))
        elements.append(bar)

    if not items:
        elements.append(Paragraph("No stock listed.", cell))
    else:
        for group in _group_by_type(items, lambda item: item.material_type):
            group_items = sorted(
                group["rows"],
                key=lambda item: ((item.material_name or "").casefold(), (item.size or "").casefold()),
            )
            names = [item.material_name for item in group_items] + [stock_headers[0], "Total"]
            sizes = [item.size or "—" for item in group_items] + [stock_headers[1]]
            microns = [item.micron or "—" for item in group_items] + [stock_headers[2]]
            rolls = [_format_num(item.rolls_left, 2) for item in group_items] + [stock_headers[3]]
            kgs = [_format_num(item.kg, 3) for item in group_items] + [stock_headers[4]]
            widths = [
                _col_width(names, minimum=0.95 * inch, maximum=2.1 * inch),
                _col_width(sizes, minimum=0.55 * inch, maximum=1.15 * inch),
                _col_width(microns, minimum=0.6 * inch, maximum=1.05 * inch),
                _col_width(rolls, minimum=0.55 * inch, maximum=0.85 * inch),
                _col_width(kgs, minimum=0.6 * inch, maximum=0.95 * inch),
            ]
            add_type_heading(group["label"], sum(widths))

            data = [[Paragraph(label, head) for label in stock_headers]]
            for item in group_items:
                data.append(
                    [
                        Paragraph(item.material_name, cell),
                        Paragraph(item.size or "—", cell),
                        Paragraph(item.micron or "—", cell),
                        Paragraph(_format_num(item.rolls_left, 2), cell_right),
                        Paragraph(_format_num(item.kg, 3), cell_right),
                    ]
                )
            group_rolls = sum(float(item.rolls_left or 0) for item in group_items)
            group_kg = sum(float(item.kg or 0) for item in group_items)
            data.append(
                [
                    Paragraph("Total", bold),
                    "",
                    "",
                    Paragraph(_format_num(group_rolls, 2), bold_right),
                    Paragraph(_format_num(group_kg, 3), bold_right),
                ]
            )
            elements.append(_styled_table(data, widths, has_total=True))

        elements.append(Spacer(1, 8))
        elements.append(
            Paragraph(
                f"Grand total · Rolls {total_rolls:,.2f} · KG {total_kg:,.3f}",
                ParagraphStyle("Grand", parent=bold, fontSize=9, spaceBefore=4),
            )
        )

    elements.append(Paragraph(movement_title, section_style))
    if not movements:
        elements.append(Paragraph("No records yet.", cell))
    else:
        move_headers = ["Date", "Material", "Size", "Micron", "KG", "Rolls"]
        if include_where_used:
            move_headers.append("Where")
        move_headers.append("Notes")

        for group in _group_by_type(movements, lambda row: row.material_type):
            rows = group["rows"]
            columns = [
                [row.movement_date.strftime("%d/%m/%Y") for row in rows],
                [row.material_name for row in rows],
                [row.size or "—" for row in rows],
                [row.micron or "—" for row in rows],
                [_format_num(row.gross_kg, 3) for row in rows],
                [_format_num(row.rolls, 2) for row in rows],
            ]
            if include_where_used:
                columns.append([row.where_used or "—" for row in rows])
            columns.append([row.notes or "—" for row in rows])
            limits = [
                (0.72 * inch, 0.95 * inch),
                (0.85 * inch, 1.7 * inch),
                (0.48 * inch, 0.85 * inch),
                (0.55 * inch, 0.8 * inch),
                (0.5 * inch, 0.8 * inch),
                (0.48 * inch, 0.7 * inch),
            ]
            if include_where_used:
                limits.append((0.7 * inch, 1.5 * inch))
            limits.append((0.6 * inch, 1.6 * inch))
            widths = [
                _col_width(values + [header], minimum=low, maximum=high)
                for values, header, (low, high) in zip(columns, move_headers, limits)
            ]
            usable = 7.2 * inch
            total_width = sum(widths)
            if total_width > usable:
                scale = usable / total_width
                widths = [width * scale for width in widths]
            add_type_heading(group["label"], sum(widths))
            data = [[Paragraph(label, head) for label in move_headers]]
            for row in rows:
                line = [
                    Paragraph(row.movement_date.strftime("%d/%m/%Y"), cell),
                    Paragraph(row.material_name, cell),
                    Paragraph(row.size or "—", cell),
                    Paragraph(row.micron or "—", cell),
                    Paragraph(_format_num(row.gross_kg, 3), cell_right),
                    Paragraph(_format_num(row.rolls, 2), cell_right),
                ]
                if include_where_used:
                    line.append(Paragraph(row.where_used or "—", cell))
                line.append(Paragraph(row.notes or "—", cell))
                data.append(line)
            elements.append(_styled_table(data, widths, right_cols=(4, 5)))

    elements.append(Spacer(1, 12))
    elements.append(
        Paragraph(
            "RN COLOUR · Printing Materials",
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
