from datetime import datetime
from io import BytesIO
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


def generate_gate_pass_pdf(gate_pass: ShGatePass) -> BytesIO:
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        topMargin=0.55 * inch,
        bottomMargin=0.55 * inch,
        leftMargin=0.65 * inch,
        rightMargin=0.65 * inch,
    )
    styles = getSampleStyleSheet()
    elements = []

    if SH_LOGO_PATH.exists():
        elements.append(
            Image(str(SH_LOGO_PATH), width=2.2 * inch, height=1.0 * inch, kind="proportional")
        )
        elements.append(Spacer(1, 0.15 * inch))

    title_style = ParagraphStyle(
        "GatePassTitle",
        parent=styles["Heading1"],
        fontSize=22,
        textColor=BRAND_RED,
        alignment=1,
        spaceAfter=4,
    )
    subtitle_style = ParagraphStyle(
        "GatePassSubtitle",
        parent=styles["Normal"],
        fontSize=11,
        textColor=BRAND_BLACK,
        alignment=1,
        spaceAfter=10,
    )
    elements.append(Paragraph("GATE PASS", title_style))
    elements.append(Paragraph(f"<b>{gate_pass.gate_pass_number}</b>", subtitle_style))

    issued = gate_pass.issued_at
    roll_count = len(gate_pass.roll_items) if gate_pass.roll_items else int(gate_pass.rolls or 0)
    meta_data = [
        ["Date", issued.strftime("%d-%m-%Y")],
        ["Time", issued.strftime("%H:%M:%S")],
        ["Sold To", gate_pass.sold_to.name],
        ["Supplier", gate_pass.supplier.name],
        ["Material", gate_pass.material_name],
        ["Size / Micron", _format_size_micron(gate_pass)],
        ["Total Rolls", f"{roll_count:,}"],
    ]
    meta_table = Table(meta_data, colWidths=[1.6 * inch, 4.6 * inch])
    meta_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (0, -1), LIGHT_BG),
                ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
                ("FONTNAME", (1, 0), (1, -1), "Helvetica"),
                ("FONTSIZE", (0, 0), (-1, -1), 10),
                ("TEXTCOLOR", (0, 0), (-1, -1), BRAND_BLACK),
                ("BOX", (0, 0), (-1, -1), 0.8, BORDER_GREY),
                ("INNERGRID", (0, 0), (-1, -1), 0.4, BORDER_GREY),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                ("TOPPADDING", (0, 0), (-1, -1), 7),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
            ]
        )
    )
    elements.append(meta_table)
    elements.append(Spacer(1, 0.2 * inch))

    roll_header = ParagraphStyle(
        "RollHeader",
        parent=styles["Normal"],
        fontSize=11,
        textColor=BRAND_RED,
        fontName="Helvetica-Bold",
        spaceAfter=6,
    )
    elements.append(Paragraph("Roll Weights (KG)", roll_header))

    roll_data = [["Roll #", "Gross Weight (KG)"]]
    if gate_pass.roll_items:
        for roll in gate_pass.roll_items:
            roll_data.append([str(roll.roll_number), f"{roll.gross_weight:,.3f}"])
    elif gate_pass.rolls and gate_pass.gross_weight_per_roll:
        for index in range(int(gate_pass.rolls)):
            roll_data.append([str(index + 1), f"{gate_pass.gross_weight_per_roll:,.3f}"])
    else:
        roll_data.append(["—", "—"])

    roll_table = Table(roll_data, colWidths=[1.2 * inch, 2.2 * inch], hAlign="LEFT")
    roll_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), BRAND_RED),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
                ("FONTSIZE", (0, 0), (-1, -1), 10),
                ("BOX", (0, 0), (-1, -1), 0.8, BORDER_GREY),
                ("INNERGRID", (0, 0), (-1, -1), 0.4, BORDER_GREY),
                ("LEFTPADDING", (0, 0), (-1, -1), 10),
                ("RIGHTPADDING", (0, 0), (-1, -1), 10),
                ("TOPPADDING", (0, 0), (-1, -1), 7),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
                ("ALIGN", (1, 1), (1, -1), "RIGHT"),
            ]
        )
    )
    elements.append(roll_table)
    elements.append(Spacer(1, 0.2 * inch))

    cone_total = _cone_total(gate_pass)
    cone_per_roll = float(gate_pass.cone_weight_per_roll or 0)
    cone_label = "Total Cone Weight (KG)"
    if cone_per_roll > 0 and roll_count:
        cone_label = f"Total Cone Weight (KG) — {cone_per_roll:,.3f} × {roll_count}"

    weight_data = [
        ["Total Gross Weight (KG)", f"{gate_pass.gross_weight:,.3f}"],
        [cone_label, f"{cone_total:,.3f}"],
        ["Total Net Weight (KG)", f"{gate_pass.net_weight:,.3f}"],
        ["Amount Per KG", f"₨ {gate_pass.amount_per_kg:,.2f}"],
        ["Total Amount", f"₨ {gate_pass.total_amount:,.2f}"],
    ]
    weight_table = Table(weight_data, colWidths=[3.0 * inch, 3.2 * inch])
    weight_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), LIGHT_BG),
                ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
                ("FONTNAME", (1, 0), (1, -1), "Helvetica"),
                ("FONTSIZE", (0, 0), (-1, -1), 11),
                ("BACKGROUND", (0, -1), (-1, -1), colors.HexColor("#FEF2F2")),
                ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
                ("TEXTCOLOR", (0, -1), (-1, -1), BRAND_RED),
                ("BOX", (0, 0), (-1, -1), 0.8, BORDER_GREY),
                ("INNERGRID", (0, 0), (-1, -1), 0.4, BORDER_GREY),
                ("LEFTPADDING", (0, 0), (-1, -1), 10),
                ("RIGHTPADDING", (0, 0), (-1, -1), 10),
                ("TOPPADDING", (0, 0), (-1, -1), 9),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 9),
                ("ALIGN", (1, 0), (1, -1), "RIGHT"),
            ]
        )
    )
    elements.append(weight_table)
    elements.append(Spacer(1, 0.15 * inch))

    formula_style = ParagraphStyle(
        "FormulaNote",
        parent=styles["Normal"],
        fontSize=9,
        textColor=colors.HexColor("#64748B"),
        alignment=1,
    )
    elements.append(
        Paragraph(
            "Net Weight = Total Gross Weight − Total Cone Weight · "
            "Total Amount = Net Weight × Amount Per KG",
            formula_style,
        )
    )

    if gate_pass.notes:
        elements.append(Spacer(1, 0.15 * inch))
        notes_style = ParagraphStyle(
            "Notes",
            parent=styles["Normal"],
            fontSize=10,
            textColor=BRAND_BLACK,
        )
        elements.append(Paragraph(f"<b>Notes:</b> {gate_pass.notes}", notes_style))

    elements.append(Spacer(1, 0.35 * inch))
    sign_data = [
        ["Authorized Signature", "Receiver Signature"],
        ["", ""],
        ["_________________________", "_________________________"],
    ]
    sign_table = Table(sign_data, colWidths=[3.1 * inch, 3.1 * inch])
    sign_table.setStyle(
        TableStyle(
            [
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 10),
                ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                ("TOPPADDING", (0, 2), (-1, 2), 18),
            ]
        )
    )
    elements.append(sign_table)

    footer_style = ParagraphStyle(
        "Footer",
        parent=styles["Normal"],
        fontSize=8,
        textColor=colors.HexColor("#94A3B8"),
        alignment=1,
    )
    elements.append(Spacer(1, 0.25 * inch))
    elements.append(
        Paragraph(
            f"Generated by Sami Hamas Traders · {datetime.now().strftime('%d %B %Y %H:%M')}",
            footer_style,
        )
    )

    doc.build(elements)
    buffer.seek(0)
    return buffer
