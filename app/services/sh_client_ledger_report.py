"""Complete client ledger report — traditional debit/credit running balance."""

from datetime import date, datetime, timedelta
from io import BytesIO
from typing import Optional

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_RIGHT
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from app.models import ShClientCompany, ShClientLedgerEntry, ShSaleInvoice
from app.services.sh_bank import get_current_sh_bank_id
from app.services.sh_ledger_sync import (
    ENTRY_PAYMENT,
    ENTRY_SALE,
    _client_ledger_entries,
    _client_sale_invoices,
    _entry_kind,
    balance_after_payment,
    balance_after_sale,
    get_client_account_balance,
)


def _format_money(value: float, blank_if_zero: bool = True) -> str:
    amount = float(value or 0)
    if blank_if_zero and amount == 0:
        return ""
    if amount.is_integer():
        return f"{int(amount):,}"
    return f"{amount:,.2f}"


def _format_balance(amount: float, balance_type: str) -> str:
    if (balance_type or "DR") == "CR":
        return f"({_format_money(amount, blank_if_zero=False)})"
    return _format_money(amount, blank_if_zero=False)


def _invoice_narration(invoice: ShSaleInvoice) -> str:
    parts = []
    if invoice.factory_challan_no:
        parts.append(f"G.P #({invoice.factory_challan_no})")
    if invoice.lines:
        net_weight = sum(float(line.net_weight or 0) for line in invoice.lines)
        first = invoice.lines[0]
        rate = float(first.unit_price or 0)
        material = first.item_name or "Material"
        parts.append(f"Wt({net_weight:,.3f}-Kgs) Rate({rate:,.0f}) {material}")
        if len(invoice.lines) > 1:
            parts.append(f"({len(invoice.lines)} line items)")
    if invoice.notes:
        parts.append(invoice.notes)
    return " ".join(parts) if parts else f"Sale Invoice {invoice.invoice_number}"


def _ledger_sale_narration(entry: ShClientLedgerEntry) -> str:
    parts = []
    if entry.factory_challan_no:
        parts.append(f"Challan #{entry.factory_challan_no}")
    if entry.lines:
        net_weight = sum(float(line.net_weight or 0) for line in entry.lines)
        first = entry.lines[0]
        parts.append(
            f"Wt({net_weight:,.3f}-Kgs) Rate({float(first.unit_price or 0):,.0f}) "
            f"{first.item_name or 'Item'}"
        )
    if entry.notes:
        parts.append(entry.notes)
    return " ".join(parts) if parts else entry.reference_number


def _payment_narration(entry: ShClientLedgerEntry) -> str:
    if entry.notes:
        return entry.notes
    if entry.source_bank_ledger_id:
        return "Bank payment received"
    return f"Payment received — {entry.reference_number}"


def build_client_ledger_timeline(
    client_id: int,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
) -> dict:
    bank_id = get_current_sh_bank_id()
    client = ShClientCompany.query.get_or_404(client_id)
    invoices = _client_sale_invoices(client_id, bank_id)
    has_invoices = len(invoices) > 0
    raw_rows = []

    if has_invoices:
        for invoice in invoices:
            raw_rows.append(
                {
                    "sort_key": (invoice.invoice_date, 0, invoice.id),
                    "date": invoice.invoice_date,
                    "type": "S10",
                    "number": invoice.invoice_number,
                    "extra": invoice.factory_challan_no or "",
                    "narration": _invoice_narration(invoice),
                    "debit": float(invoice.total_amount or 0),
                    "credit": 0.0,
                }
            )

    for entry in _client_ledger_entries(client_id):
        if _entry_kind(entry) == ENTRY_PAYMENT:
            raw_rows.append(
                {
                    "sort_key": (entry.entry_date, 1, entry.id),
                    "date": entry.entry_date,
                    "type": "BR",
                    "number": entry.reference_number,
                    "extra": "",
                    "narration": _payment_narration(entry),
                    "debit": 0.0,
                    "credit": float(entry.total_amount or 0),
                }
            )
        elif not has_invoices and _entry_kind(entry) == ENTRY_SALE:
            raw_rows.append(
                {
                    "sort_key": (entry.entry_date, 0, entry.id),
                    "date": entry.entry_date,
                    "type": "S10",
                    "number": entry.reference_number,
                    "extra": entry.factory_challan_no or "",
                    "narration": _ledger_sale_narration(entry),
                    "debit": float(entry.total_amount or 0),
                    "credit": 0.0,
                }
            )

    raw_rows.sort(key=lambda row: row["sort_key"])

    if date_from:
        opening_before = date_from - timedelta(days=1)
        opening_balance, opening_type = get_client_account_balance(
            client_id, before_date=opening_before
        )
    else:
        opening_balance, opening_type = 0.0, "DR"

    filtered = []
    for row in raw_rows:
        if date_from and row["date"] < date_from:
            continue
        if date_to and row["date"] > date_to:
            continue
        filtered.append(row)

    running = opening_balance
    running_type = opening_type
    timeline = []
    total_debit = 0.0
    total_credit = 0.0

    if date_from or date_to:
        timeline.append(
            {
                "date": date_from or (filtered[0]["date"] if filtered else date.today()),
                "type": "",
                "number": "",
                "extra": "",
                "narration": "Opening Balance :====>",
                "debit": 0.0,
                "credit": 0.0,
                "balance": running,
                "balance_type": running_type,
                "is_opening": True,
            }
        )

    for row in filtered:
        if row["debit"]:
            running, running_type = balance_after_sale(running, running_type, row["debit"])
            total_debit += row["debit"]
        if row["credit"]:
            running, running_type = balance_after_payment(running, running_type, row["credit"])
            total_credit += row["credit"]
        timeline.append(
            {
                **row,
                "balance": running,
                "balance_type": running_type,
                "is_opening": False,
            }
        )

    closing_balance, closing_type = running, running_type
    if not filtered and not (date_from or date_to):
        closing_balance, closing_type = get_client_account_balance(client_id)

    return {
        "client": client,
        "date_from": date_from,
        "date_to": date_to,
        "opening_balance": opening_balance,
        "opening_type": opening_type,
        "closing_balance": closing_balance,
        "closing_type": closing_type,
        "total_debit": total_debit,
        "total_credit": total_credit,
        "rows": timeline,
    }


def generate_complete_client_ledger_pdf(report: dict) -> BytesIO:
    client = report["client"]
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=landscape(A4),
        topMargin=0.35 * inch,
        bottomMargin=0.35 * inch,
        leftMargin=0.3 * inch,
        rightMargin=0.3 * inch,
    )
    styles = getSampleStyleSheet()
    elements = []

    brand_red = colors.HexColor("#B21E22")
    header_bg = colors.HexColor("#1A1A1A")
    alt_row = colors.HexColor("#F3F4F6")
    border = colors.HexColor("#9CA3AF")

    title_style = ParagraphStyle(
        "Title",
        parent=styles["Normal"],
        fontSize=14,
        fontName="Helvetica-Bold",
        alignment=TA_CENTER,
        spaceAfter=4,
    )
    meta_style = ParagraphStyle(
        "Meta",
        parent=styles["Normal"],
        fontSize=8,
        fontName="Helvetica",
        alignment=TA_RIGHT,
    )
    client_bar_style = ParagraphStyle(
        "ClientBar",
        parent=styles["Normal"],
        fontSize=10,
        fontName="Helvetica-Bold",
        textColor=colors.white,
    )
    cell_style = ParagraphStyle(
        "Cell",
        parent=styles["Normal"],
        fontSize=7,
        leading=9,
        fontName="Helvetica",
    )
    narr_style = ParagraphStyle(
        "Narr",
        parent=styles["Normal"],
        fontSize=7,
        leading=9,
        fontName="Helvetica",
    )
    head_style = ParagraphStyle(
        "Head",
        parent=cell_style,
        fontName="Helvetica-Bold",
        textColor=colors.white,
    )

    date_from = report["date_from"]
    date_to = report["date_to"]
    if date_from and date_to:
        range_text = f"Dated: {date_from.strftime('%d-%b-%y')} TO {date_to.strftime('%d-%b-%y')}"
    elif date_from:
        range_text = f"From: {date_from.strftime('%d-%b-%y')}"
    elif date_to:
        range_text = f"Up to: {date_to.strftime('%d-%b-%y')}"
    else:
        range_text = "Complete ledger from start"

    elements.append(Paragraph("LEDGER REPORT", title_style))
    elements.append(
        Paragraph(
            f"Printed On: {datetime.now().strftime('%A %d/%m/%Y %H:%M')}<br/>{range_text}",
            meta_style,
        )
    )
    elements.append(Spacer(1, 6))

    client_bar = Table(
        [
            [
                Paragraph(f"{client.id:08d} :===> {client.name.upper()}", client_bar_style),
                Paragraph(
                    f"Balance: {_format_balance(report['closing_balance'], report['closing_type'])}",
                    ParagraphStyle(
                        "Bal",
                        parent=client_bar_style,
                        alignment=TA_RIGHT,
                    ),
                ),
            ]
        ],
        colWidths=[9.0 * inch, 2.0 * inch],
    )
    client_bar.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), header_bg),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
            ]
        )
    )
    elements.append(client_bar)
    elements.append(Spacer(1, 6))

    header = [
        Paragraph("Date", head_style),
        Paragraph("Type", head_style),
        Paragraph("No", head_style),
        Paragraph("E-#", head_style),
        Paragraph("Narration", head_style),
        Paragraph("DEBIT", head_style),
        Paragraph("CREDIT", head_style),
        Paragraph("BALANCE", head_style),
    ]
    data = [header]

    for row in report["rows"]:
        data.append(
            [
                Paragraph(row["date"].strftime("%d-%b-%y"), cell_style),
                Paragraph(row["type"] or "—", cell_style),
                Paragraph(row["number"] or "—", cell_style),
                Paragraph(row["extra"] or "—", cell_style),
                Paragraph(row["narration"], narr_style),
                Paragraph(_format_money(row["debit"]), cell_style),
                Paragraph(_format_money(row["credit"]), cell_style),
                Paragraph(
                    _format_balance(row["balance"], row["balance_type"]),
                    ParagraphStyle(
                        "BalCell",
                        parent=cell_style,
                        alignment=TA_RIGHT,
                        fontName="Helvetica-Bold",
                    ),
                ),
            ]
        )

    totals_row = [
        Paragraph("", cell_style),
        Paragraph("", cell_style),
        Paragraph("", cell_style),
        Paragraph("", cell_style),
        Paragraph("Totals", ParagraphStyle("Tot", parent=cell_style, fontName="Helvetica-Bold")),
        Paragraph(_format_money(report["total_debit"], blank_if_zero=False), cell_style),
        Paragraph(_format_money(report["total_credit"], blank_if_zero=False), cell_style),
        Paragraph(
            _format_balance(report["closing_balance"], report["closing_type"]),
            ParagraphStyle("TotBal", parent=cell_style, fontName="Helvetica-Bold", alignment=TA_RIGHT),
        ),
    ]
    data.append(totals_row)

    col_widths = [0.75 * inch, 0.45 * inch, 0.85 * inch, 0.55 * inch, 4.8 * inch, 0.95 * inch, 0.95 * inch, 1.0 * inch]
    table = Table(data, colWidths=col_widths, repeatRows=1)
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), brand_red),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("BOX", (0, 0), (-1, -1), 1, border),
                ("INNERGRID", (0, 0), (-1, -1), 0.25, border),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("ALIGN", (5, 0), (-1, -1), "RIGHT"),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                ("BACKGROUND", (0, -1), (-1, -1), alt_row),
                ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
            ]
        )
    )
    elements.append(table)
    elements.append(Spacer(1, 8))
    elements.append(
        Paragraph(
            "Sami Hamas Traders · RN COLOUR Accounts",
            ParagraphStyle(
                "Foot",
                parent=styles["Normal"],
                fontSize=8,
                alignment=TA_CENTER,
                textColor=colors.HexColor("#6B7280"),
            ),
        )
    )

    doc.build(elements)
    buffer.seek(0)
    return buffer
