from datetime import datetime
from io import BytesIO
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from app.models import ShOrderConfirmation

SH_LOGO_PATH = Path(__file__).resolve().parent.parent / "static" / "images" / "sh-traders-logo.png"
GREY_TEXT = colors.HexColor("#6B7280")
BRAND_RED = colors.HexColor("#A31F1F")
BRAND_GREEN = colors.HexColor("#16A34A")
BRAND_BLACK = colors.HexColor("#1A1A1A")
HEADER_BG = colors.HexColor("#F0FDF4")
BORDER_GREY = colors.HexColor("#D1D5DB")


def _format_kg(value: float) -> str:
    if float(value).is_integer():
        return f"{int(value):,}"
    return f"{value:,.1f}"


def generate_order_confirmation_pdf(order: ShOrderConfirmation) -> BytesIO:
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

    title_style = ParagraphStyle(
        "OrderTitle",
        parent=styles["Normal"],
        fontSize=18,
        textColor=BRAND_BLACK,
        fontName="Helvetica-Bold",
        alignment=TA_CENTER,
        spaceAfter=4,
    )
    subtitle_style = ParagraphStyle(
        "OrderSubtitle",
        parent=styles["Normal"],
        fontSize=10,
        textColor=GREY_TEXT,
        alignment=TA_CENTER,
        spaceAfter=16,
    )
    tick_style = ParagraphStyle(
        "Tick",
        parent=styles["Normal"],
        fontSize=28,
        textColor=BRAND_GREEN,
        fontName="Helvetica-Bold",
        alignment=TA_CENTER,
        spaceAfter=8,
    )
    label_style = ParagraphStyle(
        "Label",
        parent=styles["Normal"],
        fontSize=10,
        textColor=GREY_TEXT,
        fontName="Helvetica",
        leading=14,
    )
    value_style = ParagraphStyle(
        "Value",
        parent=styles["Normal"],
        fontSize=12,
        textColor=BRAND_BLACK,
        fontName="Helvetica-Bold",
        leading=16,
    )

    if SH_LOGO_PATH.exists():
        elements.append(
            Image(str(SH_LOGO_PATH), width=1.6 * inch, height=0.6 * inch, kind="proportional")
        )
        elements.append(Spacer(1, 8))

    elements.append(Paragraph("Order Confirmation Slip", title_style))
    elements.append(
        Paragraph(
            f"Sami Hamas Traders · {datetime.now().strftime('%d %B %Y')}",
            subtitle_style,
        )
    )
    elements.append(Paragraph("✓", tick_style))
    elements.append(
        Paragraph(
            '<font color="#16A34A"><b>Order Confirmed</b></font>',
            ParagraphStyle(
                "Confirmed",
                parent=styles["Normal"],
                fontSize=12,
                alignment=TA_CENTER,
                spaceAfter=20,
            ),
        )
    )

    detail_rows = [
        [Paragraph("Material Name", label_style), Paragraph(order.material_name, value_style)],
        [Paragraph("Size", label_style), Paragraph(order.size or "—", value_style)],
        [Paragraph("Micron", label_style), Paragraph(order.micron or "—", value_style)],
        [Paragraph("KG", label_style), Paragraph(_format_kg(order.total_kg), value_style)],
        [
            Paragraph("Purchased For", label_style),
            Paragraph(order.client.name, value_style),
        ],
    ]
    if order.notes:
        detail_rows.append(
            [Paragraph("Notes", label_style), Paragraph(order.notes, value_style)]
        )

    detail_table = Table(detail_rows, colWidths=[2.2 * inch, 4.2 * inch])
    detail_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), colors.white),
                ("BOX", (0, 0), (-1, -1), 1, BORDER_GREY),
                ("INNERGRID", (0, 0), (-1, -1), 0.5, BORDER_GREY),
                ("BACKGROUND", (0, 0), (0, -1), HEADER_BG),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("TOPPADDING", (0, 0), (-1, -1), 10),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
                ("LEFTPADDING", (0, 0), (-1, -1), 12),
                ("RIGHTPADDING", (0, 0), (-1, -1), 12),
            ]
        )
    )
    elements.append(detail_table)
    elements.append(Spacer(1, 24))
    elements.append(
        Paragraph(
            "This slip confirms the order details above. Please retain for your records.",
            ParagraphStyle(
                "Footer",
                parent=styles["Normal"],
                fontSize=9,
                textColor=GREY_TEXT,
                alignment=TA_CENTER,
            ),
        )
    )

    doc.build(elements)
    buffer.seek(0)
    return buffer
