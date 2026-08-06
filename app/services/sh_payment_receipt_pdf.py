from datetime import datetime
from io import BytesIO
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from app.models import ShPaymentReceipt

SH_LOGO_PATH = Path(__file__).resolve().parent.parent / "static" / "images" / "sh-traders-logo.png"
GREY_TEXT = colors.HexColor("#6B7280")
BRAND_GREEN = colors.HexColor("#16A34A")
BRAND_BLACK = colors.HexColor("#1A1A1A")
HEADER_BG = colors.HexColor("#F0FDF4")
BORDER_GREY = colors.HexColor("#D1D5DB")
AMOUNT_BG = colors.HexColor("#ECFDF5")


def _format_money(value: float) -> str:
    if float(value).is_integer():
        return f"{int(value):,}"
    return f"{value:,.2f}"


def generate_payment_receipt_pdf(receipt: ShPaymentReceipt) -> BytesIO:
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
        "ReceiptTitle",
        parent=styles["Normal"],
        fontSize=18,
        textColor=BRAND_BLACK,
        fontName="Helvetica-Bold",
        alignment=TA_CENTER,
        spaceAfter=4,
    )
    subtitle_style = ParagraphStyle(
        "ReceiptSubtitle",
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
    amount_style = ParagraphStyle(
        "Amount",
        parent=styles["Normal"],
        fontSize=14,
        textColor=BRAND_GREEN,
        fontName="Helvetica-Bold",
        leading=18,
    )

    if SH_LOGO_PATH.exists():
        elements.append(
            Image(str(SH_LOGO_PATH), width=1.6 * inch, height=0.6 * inch, kind="proportional")
        )
        elements.append(Spacer(1, 8))

    elements.append(Paragraph("Payment Receipt Confirmation", title_style))
    elements.append(
        Paragraph(
            f"Sami Hamas Traders · Receipt #{receipt.receipt_number}",
            subtitle_style,
        )
    )
    elements.append(Paragraph("✓", tick_style))
    elements.append(
        Paragraph(
            '<font color="#16A34A"><b>Payment Received</b></font>',
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
        [
            Paragraph("Receipt Date", label_style),
            Paragraph(receipt.receipt_date.strftime("%d-%m-%Y"), value_style),
        ],
        [
            Paragraph("Client", label_style),
            Paragraph(receipt.client.name, value_style),
        ],
        [
            Paragraph("Received On This Date", label_style),
            Paragraph(f"Rs {_format_money(receipt.amount_received)}", amount_style),
        ],
        [
            Paragraph("Total Received", label_style),
            Paragraph(f"Rs {_format_money(receipt.total_received)}", value_style),
        ],
        [
            Paragraph("Total Due", label_style),
            Paragraph(f"Rs {_format_money(receipt.total_due)}", value_style),
        ],
    ]
    if receipt.notes:
        detail_rows.append(
            [Paragraph("Notes", label_style), Paragraph(receipt.notes, value_style)]
        )

    detail_table = Table(detail_rows, colWidths=[2.4 * inch, 4.0 * inch])
    detail_table.setStyle(
        TableStyle(
            [
                ("BOX", (0, 0), (-1, -1), 1, BORDER_GREY),
                ("INNERGRID", (0, 0), (-1, -1), 0.5, BORDER_GREY),
                ("BACKGROUND", (0, 0), (0, -1), HEADER_BG),
                ("BACKGROUND", (1, 2), (1, 2), AMOUNT_BG),
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
            f"Generated {datetime.now().strftime('%d-%m-%Y %H:%M')} · "
            "This receipt confirms payment received from the client named above.",
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
