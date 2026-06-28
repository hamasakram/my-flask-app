from datetime import datetime
from io import BytesIO
from math import ceil
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from app.models import ShGatePass

SH_LOGO_PATH = Path(__file__).resolve().parent.parent / "static" / "images" / "sh-traders-logo.png"
BRAND_RED = colors.HexColor("#A31F1F")
BRAND_BLACK = colors.HexColor("#1A1A1A")
BORDER_GREY = colors.HexColor("#CCCCCC")
LIGHT_BG = colors.HexColor("#F9FAFB")
ROLL_COLUMNS = 3


def _format_size_micron(gate_pass: ShGatePass) -> str:
    size = (gate_pass.size or "").strip()
    micron = (gate_pass.micron or "").strip()
    if size and micron:
        return f"{size} / {micron} micron"
    if size:
        return size
    if micron:
        return f"{micron} micron"
    return "—"


def _cone_total(gate_pass: ShGatePass) -> float:
    cone_per_roll = float(gate_pass.cone_weight_per_roll or 0)
    roll_count = int(gate_pass.rolls or 0)
    if gate_pass.roll_items:
        roll_count = len(gate_pass.roll_items)
    return cone_per_roll * roll_count


def _collect_rolls(gate_pass: ShGatePass) -> list[tuple[int, float]]:
    if gate_pass.roll_items:
        return [(roll.roll_number, roll.gross_weight) for roll in gate_pass.roll_items]
    if gate_pass.rolls and gate_pass.gross_weight_per_roll:
        return [
            (index + 1, float(gate_pass.gross_weight_per_roll))
            for index in range(int(gate_pass.rolls))
        ]
    return []


def _build_roll_table(rolls: list[tuple[int, float]]) -> Table:
    pair_width = 1.35 * inch
    label_width = 0.5 * inch
    weight_width = pair_width - label_width
    col_widths = [label_width, weight_width] * ROLL_COLUMNS

    header = []
    for _ in range(ROLL_COLUMNS):
        header.extend(["Roll", "KG"])

    body = [header]
    if not rolls:
        body.append(["—", "—"] + [""] * (ROLL_COLUMNS * 2 - 2))
    else:
        rows_needed = ceil(len(rolls) / ROLL_COLUMNS)
        for row_index in range(rows_needed):
            row = []
            for col_index in range(ROLL_COLUMNS):
                roll_index = row_index * ROLL_COLUMNS + col_index
                if roll_index < len(rolls):
                    number, weight = rolls[roll_index]
                    row.extend([str(number), f"{weight:,.3f}"])
                else:
                    row.extend(["", ""])
            body.append(row)

    table = Table(body, colWidths=col_widths, hAlign="LEFT")
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), BRAND_RED),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
                ("FONTSIZE", (0, 0), (-1, -1), 8),
                ("BOX", (0, 0), (-1, -1), 0.6, BORDER_GREY),
                ("INNERGRID", (0, 0), (-1, -1), 0.3, BORDER_GREY),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                ("ALIGN", (0, 1), (0, -1), "CENTER"),
                ("ALIGN", (2, 1), (2, -1), "CENTER"),
                ("ALIGN", (4, 1), (4, -1), "CENTER"),
                ("ALIGN", (1, 1), (1, -1), "RIGHT"),
                ("ALIGN", (3, 1), (3, -1), "RIGHT"),
                ("ALIGN", (5, 1), (5, -1), "RIGHT"),
            ]
        )
    )
    return table


def generate_gate_pass_pdf(gate_pass: ShGatePass) -> BytesIO:
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        topMargin=0.35 * inch,
        bottomMargin=0.35 * inch,
        leftMargin=0.5 * inch,
        rightMargin=0.5 * inch,
    )
    styles = getSampleStyleSheet()
    elements = []

    if SH_LOGO_PATH.exists():
        elements.append(
            Image(str(SH_LOGO_PATH), width=1.6 * inch, height=0.72 * inch, kind="proportional")
        )
        elements.append(Spacer(1, 0.06 * inch))

    title_style = ParagraphStyle(
        "GatePassTitle",
        parent=styles["Heading1"],
        fontSize=18,
        textColor=BRAND_RED,
        alignment=1,
        spaceAfter=2,
        spaceBefore=0,
    )
    subtitle_style = ParagraphStyle(
        "GatePassSubtitle",
        parent=styles["Normal"],
        fontSize=10,
        textColor=BRAND_BLACK,
        alignment=1,
        spaceAfter=6,
    )
    elements.append(Paragraph("GATE PASS", title_style))
    elements.append(Paragraph(f"<b>{gate_pass.gate_pass_number}</b>", subtitle_style))

    issued = gate_pass.issued_at
    rolls = _collect_rolls(gate_pass)
    roll_count = len(rolls) if rolls else int(gate_pass.rolls or 0)
    meta_data = [
        ["Date", issued.strftime("%d-%m-%Y"), "Time", issued.strftime("%H:%M:%S")],
        ["Sold To", gate_pass.sold_to.name, "Total Rolls", f"{roll_count:,}"],
        ["Material", gate_pass.material_name, "Size / Micron", _format_size_micron(gate_pass)],
    ]
    meta_table = Table(meta_data, colWidths=[0.95 * inch, 2.35 * inch, 0.95 * inch, 2.35 * inch])
    meta_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (0, -1), LIGHT_BG),
                ("BACKGROUND", (2, 0), (2, -1), LIGHT_BG),
                ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
                ("FONTNAME", (2, 0), (2, -1), "Helvetica-Bold"),
                ("FONTNAME", (1, 0), (1, -1), "Helvetica"),
                ("FONTNAME", (3, 0), (3, -1), "Helvetica"),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("TEXTCOLOR", (0, 0), (-1, -1), BRAND_BLACK),
                ("BOX", (0, 0), (-1, -1), 0.6, BORDER_GREY),
                ("INNERGRID", (0, 0), (-1, -1), 0.3, BORDER_GREY),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 5),
                ("RIGHTPADDING", (0, 0), (-1, -1), 5),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    elements.append(meta_table)
    elements.append(Spacer(1, 0.1 * inch))

    roll_header = ParagraphStyle(
        "RollHeader",
        parent=styles["Normal"],
        fontSize=9,
        textColor=BRAND_RED,
        fontName="Helvetica-Bold",
        spaceAfter=3,
        spaceBefore=0,
    )
    elements.append(Paragraph("Roll Weights (KG)", roll_header))
    elements.append(_build_roll_table(rolls))
    elements.append(Spacer(1, 0.1 * inch))

    cone_total = _cone_total(gate_pass)
    cone_per_roll = float(gate_pass.cone_weight_per_roll or 0)
    cone_label = "Total Cone Weight (KG)"
    if cone_per_roll > 0 and roll_count:
        cone_label = f"Cone Weight (KG) — {cone_per_roll:,.3f} × {roll_count}"

    weight_data = [
        ["Total Gross Weight (KG)", f"{gate_pass.gross_weight:,.3f}"],
        [cone_label, f"{cone_total:,.3f}"],
        ["Total Net Weight (KG)", f"{gate_pass.net_weight:,.3f}"],
        ["Amount Per KG", f"₨ {gate_pass.amount_per_kg:,.2f}"],
        ["Total Amount", f"₨ {gate_pass.total_amount:,.2f}"],
    ]
    weight_table = Table(weight_data, colWidths=[3.0 * inch, 3.7 * inch])
    weight_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (0, -1), LIGHT_BG),
                ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
                ("FONTNAME", (1, 0), (1, -1), "Helvetica"),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("BACKGROUND", (0, -1), (-1, -1), colors.HexColor("#FEF2F2")),
                ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
                ("TEXTCOLOR", (0, -1), (-1, -1), BRAND_RED),
                ("BOX", (0, 0), (-1, -1), 0.6, BORDER_GREY),
                ("INNERGRID", (0, 0), (-1, -1), 0.3, BORDER_GREY),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ("ALIGN", (1, 0), (1, -1), "RIGHT"),
            ]
        )
    )
    elements.append(weight_table)

    if gate_pass.notes:
        elements.append(Spacer(1, 0.08 * inch))
        notes_style = ParagraphStyle(
            "Notes",
            parent=styles["Normal"],
            fontSize=8,
            textColor=BRAND_BLACK,
        )
        elements.append(Paragraph(f"<b>Notes:</b> {gate_pass.notes}", notes_style))

    elements.append(Spacer(1, 0.15 * inch))
    sign_data = [
        ["Authorized Signature", "Receiver Signature"],
        ["_________________________", "_________________________"],
    ]
    sign_table = Table(sign_data, colWidths=[3.25 * inch, 3.25 * inch])
    sign_table.setStyle(
        TableStyle(
            [
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                ("TOPPADDING", (0, 1), (-1, 1), 10),
                ("BOTTOMPADDING", (0, 0), (-1, 0), 2),
            ]
        )
    )
    elements.append(sign_table)

    footer_style = ParagraphStyle(
        "Footer",
        parent=styles["Normal"],
        fontSize=7,
        textColor=colors.HexColor("#94A3B8"),
        alignment=1,
        spaceBefore=4,
    )
    elements.append(
        Paragraph(
            f"Sami Hamas Traders · {datetime.now().strftime('%d %B %Y %H:%M')}",
            footer_style,
        )
    )

    doc.build(elements)
    buffer.seek(0)
    return buffer
