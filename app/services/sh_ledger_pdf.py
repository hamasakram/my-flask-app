from datetime import datetime
from io import BytesIO
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

SH_LOGO_PATH = Path(__file__).resolve().parent.parent / "static" / "images" / "sh-traders-logo.png"
GREY_TEXT = colors.HexColor("#6B7280")
BRAND_RED = colors.HexColor("#A31F1F")
BRAND_BLACK = colors.HexColor("#1A1A1A")
HEADER_BG = colors.HexColor("#E5E7EB")
BORDER_GREY = colors.HexColor("#D1D5DB")


def _format_money(value: float) -> str:
    if float(value).is_integer():
        return f"{int(value):,}"
    return f"{value:,.2f}"


def _format_balance(value: float, balance_type: str) -> str:
    return f"{_format_money(value)} {balance_type or 'DR'}"


def _ledger_line_rows(entry) -> list[list]:
    rows = [
        [
            "Item",
            "Size",
            "Qty",
            "Unit",
            "Gross (KG)",
            "Net (KG)",
            "Unit Price",
            "Total",
        ]
    ]
    for line in entry.lines:
        rows.append(
            [
                line.item_name,
                line.size or "—",
                _format_money(line.qty),
                line.qty_unit,
                _format_money(line.gross_weight),
                _format_money(line.net_weight),
                _format_money(line.unit_price),
                _format_money(line.line_total),
            ]
        )
    return rows


def _build_ledger_pdf(
    entries: list,
    pdf_title: str,
    party_label: str,
    party_name_fn,
) -> BytesIO:
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=landscape(A4),
        topMargin=0.4 * inch,
        bottomMargin=0.4 * inch,
        leftMargin=0.45 * inch,
        rightMargin=0.45 * inch,
    )
    styles = getSampleStyleSheet()
    elements = []

    title_style = ParagraphStyle(
        "LedgerTitle",
        parent=styles["Normal"],
        fontSize=16,
        textColor=BRAND_RED,
        fontName="Helvetica-Bold",
        alignment=TA_CENTER,
        spaceAfter=6,
    )
    subtitle_style = ParagraphStyle(
        "LedgerSubtitle",
        parent=styles["Normal"],
        fontSize=9,
        textColor=GREY_TEXT,
        alignment=TA_CENTER,
        spaceAfter=12,
    )
    meta_style = ParagraphStyle(
        "Meta",
        parent=styles["Normal"],
        fontSize=9,
        fontName="Helvetica-Bold",
        leading=12,
    )

    if SH_LOGO_PATH.exists():
        elements.append(
            Image(str(SH_LOGO_PATH), width=1.4 * inch, height=0.55 * inch, kind="proportional")
        )
        elements.append(Spacer(1, 4))

    elements.append(Paragraph(pdf_title, title_style))
    elements.append(
        Paragraph(
            f"Generated {datetime.now().strftime('%d-%m-%Y %H:%M')} · Sami Hamas Traders",
            subtitle_style,
        )
    )

    if not entries:
        elements.append(Paragraph("No ledger entries recorded.", meta_style))
        doc.build(elements)
        buffer.seek(0)
        return buffer

    for entry in entries:
        header_data = [
            [
                Paragraph(f"<b>Date:</b> {entry.entry_date.strftime('%d-%m-%Y')}", meta_style),
                Paragraph(f"<b>Ref #:</b> {entry.reference_number}", meta_style),
                Paragraph(
                    f"<b>{party_label}:</b> {party_name_fn(entry)}",
                    meta_style,
                ),
            ],
            [
                Paragraph(
                    f"<b>Challan:</b> {entry.factory_challan_no or '—'}",
                    meta_style,
                ),
                Paragraph(f"<b>Location:</b> {entry.location}", meta_style),
                Paragraph(
                    f"<b>Total:</b> Rs {_format_money(entry.total_amount)}",
                    meta_style,
                ),
            ],
        ]
        header_table = Table(header_data, colWidths=[3.2 * inch, 3.2 * inch, 3.2 * inch])
        header_table.setStyle(
            TableStyle(
                [
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ]
            )
        )
        elements.append(header_table)
        elements.append(Spacer(1, 4))

        line_table = Table(
            _ledger_line_rows(entry),
            repeatRows=1,
            colWidths=[
                2.8 * inch,
                0.7 * inch,
                0.6 * inch,
                0.8 * inch,
                0.9 * inch,
                0.9 * inch,
                0.9 * inch,
                0.9 * inch,
            ],
        )
        line_table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), HEADER_BG),
                    ("TEXTCOLOR", (0, 0), (-1, 0), BRAND_BLACK),
                    ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                    ("FONTSIZE", (0, 0), (-1, -1), 8),
                    ("GRID", (0, 0), (-1, -1), 0.5, BORDER_GREY),
                    ("ALIGN", (2, 1), (-1, -1), "RIGHT"),
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                    ("TOPPADDING", (0, 0), (-1, -1), 4),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ]
            )
        )
        elements.append(line_table)

        balance_row = [
            [
                Paragraph(
                    f"<b>Previous Balance:</b> {_format_balance(entry.previous_balance, entry.previous_balance_type)}",
                    meta_style,
                ),
                Paragraph(
                    f"<b>Current Balance:</b> {_format_balance(entry.current_balance, entry.current_balance_type)}",
                    meta_style,
                ),
            ]
        ]
        balance_table = Table(balance_row, colWidths=[4.8 * inch, 4.8 * inch])
        balance_table.setStyle(TableStyle([("ALIGN", (1, 0), (1, 0), "RIGHT")]))
        elements.append(Spacer(1, 4))
        elements.append(balance_table)

        if entry.notes:
            elements.append(
                Paragraph(f"<b>Notes:</b> {entry.notes}", meta_style)
            )
        elements.append(Spacer(1, 16))

    doc.build(elements)
    buffer.seek(0)
    return buffer


def generate_client_ledger_pdf(entries: list) -> BytesIO:
    return _build_ledger_pdf(
        entries,
        pdf_title="PDF Sales Ledger",
        party_label="Client",
        party_name_fn=lambda e: e.sold_to.name,
    )


def generate_supplier_ledger_pdf(entries: list) -> BytesIO:
    return _build_ledger_pdf(
        entries,
        pdf_title="Supplier Ledger",
        party_label="Supplier",
        party_name_fn=lambda e: e.supplier.name,
    )
