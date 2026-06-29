from datetime import date
from io import BytesIO
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from app.services.bank_ledger import _format_money, get_rokar_day_data

LOGO_PATH = Path(__file__).resolve().parent.parent / "static" / "images" / "rn-colour-logo.png"
BRAND_RED = colors.HexColor("#B21E22")
BRAND_BLACK = colors.HexColor("#1A1A1A")
HEADER_BG = BRAND_RED
BORDER_GREY = colors.HexColor("#D1D5DB")
ALT_ROW = colors.HexColor("#F9FAFB")


def generate_rokar_pdf(entry_date: date) -> BytesIO:
    data = get_rokar_day_data(entry_date)
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
        "RokarTitle",
        parent=styles["Normal"],
        fontSize=16,
        textColor=BRAND_RED,
        fontName="Helvetica-Bold",
        leading=18,
        alignment=TA_LEFT,
    )
    subtitle_style = ParagraphStyle(
        "RokarSubtitle",
        parent=styles["Normal"],
        fontSize=10,
        textColor=BRAND_BLACK,
        fontName="Helvetica",
        leading=12,
        alignment=TA_LEFT,
    )
    meta_style = ParagraphStyle(
        "RokarMeta",
        parent=styles["Normal"],
        fontSize=9,
        textColor=BRAND_BLACK,
        fontName="Helvetica-Bold",
        leading=11,
        alignment=TA_RIGHT,
    )
    cell_style = ParagraphStyle(
        "RokarCell",
        parent=styles["Normal"],
        fontSize=8,
        leading=10,
        alignment=TA_LEFT,
    )

    header_left = Table(
        [
            [Paragraph("DAILY ROKAR ROZNAMCHA", title_style)],
            [Paragraph("RN COLOUR — Bank Ledger", subtitle_style)],
            [Paragraph(f"Date: {entry_date.strftime('%d-%b-%Y').upper()}", subtitle_style)],
        ],
        colWidths=[5.5 * inch],
    )
    header_left.setStyle(
        TableStyle(
            [
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
            ]
        )
    )

    logo_cell = ""
    if LOGO_PATH.exists():
        logo_cell = Image(str(LOGO_PATH), width=1.8 * inch, height=0.72 * inch, kind="proportional")

    header_right = Table(
        [
            [logo_cell],
            [Paragraph(f"Transactions: {data['transaction_count']}", meta_style)],
            [Paragraph(f"Total Balance: Rs {_format_money(data['total_closing'])}", meta_style)],
        ],
        colWidths=[2.2 * inch],
    )
    header_right.setStyle(
        TableStyle(
            [
                ("ALIGN", (0, 0), (-1, -1), "RIGHT"),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
            ]
        )
    )

    top_table = Table([[header_left, header_right]], colWidths=[5.8 * inch, 2.4 * inch])
    top_table.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP")]))
    elements.append(top_table)
    elements.append(Spacer(1, 0.15 * inch))

    summary_data = [
        ["Opening (Start of Day)", f"Rs {_format_money(data['total_opening_today'])}"],
        ["Total Deposits", f"Rs {_format_money(data['total_deposits'])}"],
        ["Total Withdrawals", f"Rs {_format_money(data['total_withdrawals'])}"],
        ["Closing (All Accounts)", f"Rs {_format_money(data['total_closing'])}"],
    ]
    summary_table = Table(summary_data, colWidths=[1.8 * inch, 1.35 * inch], hAlign="LEFT")
    summary_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), HEADER_BG),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, -1), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("BOX", (0, 0), (-1, -1), 0.6, BORDER_GREY),
                ("INNERGRID", (0, 0), (-1, -1), 0.4, BORDER_GREY),
                ("ALIGN", (1, 0), (1, -1), "RIGHT"),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )
    elements.append(summary_table)
    elements.append(Spacer(1, 0.15 * inch))

    txn_header = ["#", "Bank Account", "Type", "Sent To / Particulars", "Deposit", "Withdrawal"]
    txn_rows = [txn_header]
    for index, row in enumerate(data["transactions"], start=1):
        txn_rows.append(
            [
                str(index),
                row["bank"].display_name,
                row["type_label"],
                Paragraph(row["particulars"], cell_style),
                f"Rs {_format_money(row['deposit'])}" if row["deposit"] else "—",
                f"Rs {_format_money(row['withdrawal'])}" if row["withdrawal"] else "—",
            ]
        )
    if len(txn_rows) == 1:
        txn_rows.append(["—", "No transactions on this date", "", "", "—", "—"])

    txn_table = Table(
        txn_rows,
        colWidths=[0.35 * inch, 1.55 * inch, 0.85 * inch, 3.35 * inch, 1.0 * inch, 1.0 * inch],
        repeatRows=1,
    )
    txn_style = [
        ("BACKGROUND", (0, 0), (-1, 0), HEADER_BG),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("TEXTCOLOR", (0, 1), (-1, -1), BRAND_BLACK),
        ("BOX", (0, 0), (-1, -1), 0.6, BORDER_GREY),
        ("INNERGRID", (0, 0), (-1, -1), 0.4, BORDER_GREY),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN", (0, 0), (0, -1), "CENTER"),
        ("ALIGN", (4, 0), (-1, -1), "RIGHT"),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]
    for row_index in range(1, len(txn_rows)):
        if row_index % 2 == 0:
            txn_style.append(("BACKGROUND", (0, row_index), (-1, row_index), ALT_ROW))
    txn_table.setStyle(TableStyle(txn_style))
    elements.append(Paragraph("Daily Transactions", subtitle_style))
    elements.append(Spacer(1, 0.05 * inch))
    elements.append(txn_table)
    elements.append(Spacer(1, 0.18 * inch))

    balance_header = [
        "Bank Account",
        "Account Title",
        "Opening Today",
        "Deposits",
        "Withdrawals",
        "Closing Balance",
    ]
    balance_rows = [balance_header]
    for item in data["bank_balances"]:
        balance_rows.append(
            [
                item["bank"].display_name,
                item["bank"].account_title or "—",
                f"Rs {_format_money(item['opening_today'])}",
                f"Rs {_format_money(item['day_deposits'])}",
                f"Rs {_format_money(item['day_withdrawals'])}",
                f"Rs {_format_money(item['closing_balance'])}",
            ]
        )
    balance_rows.append(
        [
            "GRAND TOTAL",
            "",
            f"Rs {_format_money(data['total_opening_today'])}",
            f"Rs {_format_money(data['total_deposits'])}",
            f"Rs {_format_money(data['total_withdrawals'])}",
            f"Rs {_format_money(data['total_closing'])}",
        ]
    )

    balance_table = Table(
        balance_rows,
        colWidths=[1.7 * inch, 1.35 * inch, 1.15 * inch, 1.0 * inch, 1.0 * inch, 1.15 * inch],
        repeatRows=1,
    )
    balance_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), HEADER_BG),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTNAME", (0, 1), (-1, -2), "Helvetica"),
                ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
                ("BACKGROUND", (0, -1), (-1, -1), colors.HexColor("#FEE2E2")),
                ("FONTSIZE", (0, 0), (-1, -1), 8),
                ("TEXTCOLOR", (0, 1), (-1, -1), BRAND_BLACK),
                ("BOX", (0, 0), (-1, -1), 0.6, BORDER_GREY),
                ("INNERGRID", (0, 0), (-1, -1), 0.4, BORDER_GREY),
                ("ALIGN", (2, 0), (-1, -1), "RIGHT"),
                ("LEFTPADDING", (0, 0), (-1, -1), 5),
                ("RIGHTPADDING", (0, 0), (-1, -1), 5),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )
    elements.append(Paragraph("Bank Account Balances", subtitle_style))
    elements.append(Spacer(1, 0.05 * inch))
    elements.append(balance_table)

    doc.build(elements)
    buffer.seek(0)
    return buffer
