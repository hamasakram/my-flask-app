from datetime import datetime
from io import BytesIO
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from app.models import ShSaleInvoice

SH_LOGO_PATH = Path(__file__).resolve().parent.parent / "static" / "images" / "sh-traders-logo.png"
GREY_TEXT = colors.HexColor("#6B7280")
BRAND_BLACK = colors.HexColor("#1A1A1A")
HEADER_BG = colors.HexColor("#E5E7EB")
BORDER_GREY = colors.HexColor("#D1D5DB")


def _format_money(value: float) -> str:
    if float(value).is_integer():
        return f"{int(value):,}"
    return f"{value:,.2f}"


def _format_balance(value: float, balance_type: str) -> str:
    return f"{_format_money(value)} {balance_type or 'DR'}"


def generate_sale_invoice_pdf(invoice: ShSaleInvoice) -> BytesIO:
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        topMargin=0.45 * inch,
        bottomMargin=0.45 * inch,
        leftMargin=0.55 * inch,
        rightMargin=0.55 * inch,
    )
    styles = getSampleStyleSheet()
    elements = []

    title_style = ParagraphStyle(
        "InvoiceTitle",
        parent=styles["Normal"],
        fontSize=13,
        textColor=GREY_TEXT,
        fontName="Helvetica-Bold",
        leading=15,
        spaceAfter=2,
    )
    company_style = ParagraphStyle(
        "CompanyName",
        parent=styles["Normal"],
        fontSize=11,
        textColor=GREY_TEXT,
        fontName="Helvetica-Bold",
        leading=13,
        spaceAfter=2,
    )
    location_style = ParagraphStyle(
        "Location",
        parent=styles["Normal"],
        fontSize=10,
        textColor=GREY_TEXT,
        fontName="Helvetica-Bold",
        leading=12,
    )
    meta_style = ParagraphStyle(
        "InvoiceMeta",
        parent=styles["Normal"],
        fontSize=9,
        fontName="Helvetica-Bold",
        leading=12,
        alignment=TA_RIGHT,
    )

    header_left = Table(
        [
            [Paragraph("SALE INVOICE", title_style)],
            [Paragraph("SAMI HAMAS TRADERS", company_style)],
            [Paragraph(invoice.location or "MULTAN", location_style)],
        ],
        colWidths=[4.6 * inch],
    )
    header_left.setStyle(
        TableStyle(
            [
                ("ALIGN", (0, 0), (-1, -1), "LEFT"),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 1),
            ]
        )
    )

    logo_width = 1.55 * inch
    right_col_width = logo_width
    logo_cell = ""
    if SH_LOGO_PATH.exists():
        logo_cell = Image(str(SH_LOGO_PATH), width=logo_width, height=0.72 * inch, kind="proportional")

    header_right = Table(
        [
            [logo_cell],
            [Paragraph(f"Date: {invoice.invoice_date.strftime('%d-%b-%Y').upper()}", meta_style)],
            [Paragraph(f"Invoice: {invoice.invoice_number}", meta_style)],
            [
                Paragraph(
                    f"Factory Challan No: {invoice.factory_challan_no or '—'}",
                    meta_style,
                )
            ],
        ],
        colWidths=[right_col_width],
    )
    header_right.setStyle(
        TableStyle(
            [
                ("ALIGN", (0, 0), (-1, -1), "RIGHT"),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ("TOPPADDING", (0, 0), (0, 0), 0),
                ("TOPPADDING", (0, 1), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 1),
            ]
        )
    )

    top_table = Table(
        [[header_left, header_right]],
        colWidths=[4.6 * inch, 2.2 * inch],
        hAlign="LEFT",
    )
    top_table.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("ALIGN", (1, 0), (1, 0), "RIGHT"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
            ]
        )
    )
    elements.append(top_table)
    elements.append(Spacer(1, 0.18 * inch))

    sold_to_style = ParagraphStyle(
        "SoldTo",
        parent=styles["Normal"],
        fontSize=11,
        leading=14,
    )
    sold_to_box = Table(
        [[Paragraph(f"<b>SOLD TO:</b> {invoice.sold_to.name.upper()}", sold_to_style)]],
        colWidths=[6.8 * inch],
    )
    sold_to_box.setStyle(
        TableStyle(
            [
                ("BOX", (0, 0), (-1, -1), 1, BORDER_GREY),
                ("LEFTPADDING", (0, 0), (-1, -1), 10),
                ("RIGHTPADDING", (0, 0), (-1, -1), 10),
                ("TOPPADDING", (0, 0), (-1, -1), 8),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
            ]
        )
    )
    elements.append(sold_to_box)
    elements.append(Spacer(1, 0.12 * inch))

    table_data = [
        [
            "ITEM",
            "SIZE",
            "QTY",
            "GROSS WEIGHT",
            "NET WEIGHT",
            "UNIT PRICE",
            "TOTAL",
        ]
    ]
    for line in invoice.lines:
        qty_label = f"{line.qty:g} {line.qty_unit}" if line.qty else line.qty_unit
        table_data.append(
            [
                line.item_name,
                line.size or "—",
                qty_label,
                f"{line.gross_weight:,.3f} kg",
                f"{line.net_weight:,.3f} kg",
                f"Rs {_format_money(line.unit_price)}",
                f"Rs {_format_money(line.line_total)}",
            ]
        )

    col_widths = [2.0 * inch, 0.55 * inch, 0.85 * inch, 0.95 * inch, 0.95 * inch, 0.75 * inch, 0.75 * inch]
    items_table = Table(table_data, colWidths=col_widths, repeatRows=1)
    items_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), HEADER_BG),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
                ("FONTSIZE", (0, 0), (-1, -1), 8),
                ("TEXTCOLOR", (0, 0), (-1, -1), BRAND_BLACK),
                ("BOX", (0, 0), (-1, -1), 0.8, BORDER_GREY),
                ("INNERGRID", (0, 0), (-1, -1), 0.4, BORDER_GREY),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                ("ALIGN", (2, 1), (2, -1), "CENTER"),
                ("ALIGN", (3, 1), (-1, -1), "RIGHT"),
            ]
        )
    )
    elements.append(items_table)
    elements.append(Spacer(1, 0.12 * inch))

    totals_data = [
        ["TOTAL", f"Rs {_format_money(invoice.total_amount)}"],
        [
            "PREVIOUS BALANCE",
            _format_balance(invoice.previous_balance, invoice.previous_balance_type),
        ],
        [
            "CURRENT BALANCE",
            _format_balance(invoice.current_balance, invoice.current_balance_type),
        ],
    ]
    totals_table = Table(totals_data, colWidths=[1.5 * inch, 1.35 * inch], hAlign="RIGHT")
    totals_table.setStyle(
        TableStyle(
            [
                ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
                ("FONTNAME", (1, 0), (1, -1), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 10),
                ("BOX", (0, 0), (-1, -1), 0.8, BORDER_GREY),
                ("INNERGRID", (0, 0), (-1, -1), 0.4, BORDER_GREY),
                ("BACKGROUND", (0, 0), (-1, 0), colors.white),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                ("ALIGN", (1, 0), (1, -1), "RIGHT"),
            ]
        )
    )
    elements.append(totals_table)
    elements.append(Spacer(1, 0.25 * inch))

    footer_style = ParagraphStyle(
        "FooterThanks",
        parent=styles["Normal"],
        fontSize=10,
        textColor=BRAND_BLACK,
    )
    elements.append(Paragraph("Thank you for your business!", footer_style))
    elements.append(Spacer(1, 0.35 * inch))
    elements.append(Paragraph("Signature: _________________________", footer_style))

    if invoice.notes:
        elements.append(Spacer(1, 0.15 * inch))
        elements.append(Paragraph(f"<b>Notes:</b> {invoice.notes}", footer_style))

    doc.build(elements)
    buffer.seek(0)
    return buffer
