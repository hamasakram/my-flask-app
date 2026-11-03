"""Complete supplier ledger report — same layout as the client "Ledger Report Complete".

Accounting view of the supplier's account in our books:
  * Purchase from the supplier  -> CREDIT (we owe them more)
  * Payment made to the supplier -> DEBIT  (we owe them less)
  * Balance = purchases - payments. A normal (payable) balance is shown as a plain number;
    an advance (we paid more than we bought) is shown in brackets, like CR on the client report.

Every payment row carries its details: which bank it was paid from, the bank ledger
reference and note, and whether a payment screenshot (proof) is on file.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from io import BytesIO
from typing import Optional
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_RIGHT
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from app import db
from app.models import (
    ShBank,
    ShLedgerEntry,
    ShPaymentScreenshot,
    ShSupplierCompany,
    ShSupplierLedgerEntry,
)
from app.services.sh_bank import filter_by_bank
from app.services.sh_client_ledger_report import _format_money, _qty_size_text
from app.services.sh_ledger_sync import (
    ENTRY_PAYMENT,
    _entry_kind,
    _supplier_ledger_entries,
    get_supplier_account_balance,
)

PKT = timezone(timedelta(hours=5))

TYPE_PURCHASE = "P10"
TYPE_BANK_PAYMENT = "BP"
TYPE_PAYMENT = "PY"

# Internal balance type: "DR" = we still owe the supplier (payable), "CR" = advance paid.
PAYABLE = "DR"
ADVANCE = "CR"


def format_supplier_balance(amount: float, balance_type: str) -> str:
    """Payable as a plain number, advance in brackets (mirrors the client report)."""
    text = _format_money(amount, blank_if_zero=False)
    return f"({text})" if (balance_type or PAYABLE) == ADVANCE else text


def balance_label(balance_type: str) -> str:
    return "Advance paid" if balance_type == ADVANCE else "Payable"


def _signed(amount: float, balance_type: str) -> float:
    return -float(amount or 0) if balance_type == ADVANCE else float(amount or 0)


def _unsigned(signed: float) -> tuple[float, str]:
    return (signed, PAYABLE) if signed >= 0 else (abs(signed), ADVANCE)


def _purchase_narration(entry: ShSupplierLedgerEntry) -> str:
    parts = []
    if entry.factory_challan_no:
        parts.append(f"G.P #({entry.factory_challan_no})")
    if entry.lines:
        net_weight = sum(float(line.net_weight or 0) for line in entry.lines)
        first = entry.lines[0]
        parts.append(
            f"Wt({net_weight:,.3f}-Kgs) Rate({float(first.unit_price or 0):,.0f}) "
            f"{first.item_name or 'Material'}"
        )
        qty_size = _qty_size_text(entry.lines)
        if qty_size:
            parts.append(qty_size)
        if len(entry.lines) > 1:
            parts.append(f"({len(entry.lines)} line items)")
    if entry.notes:
        parts.append(entry.notes)
    return " ".join(parts) if parts else f"Purchase {entry.reference_number}"


def _clean_bank_note(note: str | None) -> str:
    text = (note or "").strip()
    for prefix in ("Payment sent (bank ledger):", "Payment sent (bank ledger)"):
        if text.startswith(prefix):
            text = text[len(prefix):].strip()
    return text


def _payment_screenshots(supplier_id: int) -> list[ShPaymentScreenshot]:
    query = ShPaymentScreenshot.query.filter(
        ShPaymentScreenshot.supplier_company_id == supplier_id
    )
    query = filter_by_bank(query, ShPaymentScreenshot)
    return query.order_by(ShPaymentScreenshot.payment_date.asc(), ShPaymentScreenshot.id.asc()).all()


def _match_proof(entry_date: date, amount: float, proofs: list, used: set) -> Optional[ShPaymentScreenshot]:
    """Payment screenshot for the same supplier, date and amount (each proof used once)."""
    for proof in proofs:
        if proof.id in used or proof.payment_date != entry_date:
            continue
        if proof.amount_paid is not None and abs(float(proof.amount_paid) - amount) > 0.5:
            continue
        used.add(proof.id)
        return proof
    return None


def _payment_details(entry: ShSupplierLedgerEntry, proofs: list, used: set, banks: dict) -> dict:
    amount = float(entry.total_amount or 0)
    bank_entry = (
        db.session.get(ShLedgerEntry, entry.source_bank_ledger_id)
        if entry.source_bank_ledger_id
        else None
    )
    bank = banks.get(bank_entry.bank_id if bank_entry else entry.bank_id)
    bank_name = bank.name if bank else ""
    if bank_entry is not None:
        method = f"Bank payment from {bank_name}" if bank_name else "Bank payment"
        note = _clean_bank_note(bank_entry.notes) or _clean_bank_note(entry.notes)
        reference = f"Bank ledger #{bank_entry.id}"
    else:
        method = "Manual payment entry"
        bank_name = ""
        note = (entry.notes or "").strip()
        reference = ""
    proof = _match_proof(entry.entry_date, amount, proofs, used)

    parts = [method] + ([reference] if reference else [])
    if note:
        parts.append(note)
    parts.append("Proof: screenshot on file" if proof else "Proof: not uploaded")
    return {
        "method": method,
        "bank_name": bank_name,
        "reference": reference,
        "note": note,
        "proof": proof,
        "narration": " — ".join(parts),
    }


def build_supplier_ledger_timeline(
    supplier_id: int,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
) -> dict:
    supplier = ShSupplierCompany.query.get_or_404(supplier_id)
    banks = {bank.id: bank for bank in ShBank.query.all()}
    proofs = _payment_screenshots(supplier_id)
    used_proofs: set[int] = set()

    raw_rows = []
    for entry in _supplier_ledger_entries(supplier_id):
        amount = float(entry.total_amount or 0)
        if _entry_kind(entry) == ENTRY_PAYMENT:
            details = _payment_details(entry, proofs, used_proofs, banks)
            raw_rows.append(
                {
                    "sort_key": (entry.entry_date, 1, entry.id),
                    "date": entry.entry_date,
                    "type": TYPE_BANK_PAYMENT if entry.source_bank_ledger_id else TYPE_PAYMENT,
                    "number": entry.reference_number,
                    "extra": "",
                    "narration": details["narration"],
                    "debit": amount,
                    "credit": 0.0,
                    "is_payment": True,
                    "payment": details,
                }
            )
        else:
            raw_rows.append(
                {
                    "sort_key": (entry.entry_date, 0, entry.id),
                    "date": entry.entry_date,
                    "type": TYPE_PURCHASE,
                    "number": entry.reference_number,
                    "extra": entry.factory_challan_no or "",
                    "narration": _purchase_narration(entry),
                    "debit": 0.0,
                    "credit": amount,
                    "is_payment": False,
                    "payment": None,
                }
            )
    raw_rows.sort(key=lambda row: row["sort_key"])

    if date_from:
        opening_balance, opening_type = get_supplier_account_balance(
            supplier_id, before_date=date_from - timedelta(days=1)
        )
    else:
        opening_balance, opening_type = 0.0, PAYABLE

    filtered = [
        row for row in raw_rows
        if not (date_from and row["date"] < date_from) and not (date_to and row["date"] > date_to)
    ]

    timeline = []
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
                "balance": opening_balance,
                "balance_type": opening_type,
                "is_opening": True,
                "is_payment": False,
                "payment": None,
            }
        )

    running = _signed(opening_balance, opening_type)
    total_debit = total_credit = 0.0
    payments = []
    for row in filtered:
        running += row["credit"] - row["debit"]
        total_credit += row["credit"]
        total_debit += row["debit"]
        balance, balance_type = _unsigned(running)
        timeline_row = {**row, "balance": balance, "balance_type": balance_type, "is_opening": False}
        timeline.append(timeline_row)
        if row["is_payment"]:
            payments.append(timeline_row)

    closing_balance, closing_type = _unsigned(running)
    if not filtered and not (date_from or date_to):
        closing_balance, closing_type = get_supplier_account_balance(supplier_id)

    return {
        "supplier": supplier,
        "date_from": date_from,
        "date_to": date_to,
        "opening_balance": opening_balance,
        "opening_type": opening_type,
        "closing_balance": closing_balance,
        "closing_type": closing_type,
        "total_debit": total_debit,      # paid
        "total_credit": total_credit,    # purchased
        "purchase_count": sum(1 for row in filtered if not row["is_payment"]),
        "payment_count": len(payments),
        "proof_count": sum(1 for row in payments if row["payment"]["proof"]),
        "payments": payments,
        "rows": timeline,
    }


# --------------------------------------------------------------------------- PDF


def generate_complete_supplier_ledger_pdf(report: dict) -> BytesIO:
    supplier = report["supplier"]
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=landscape(A4),
        topMargin=0.35 * inch,
        bottomMargin=0.35 * inch,
        leftMargin=0.3 * inch,
        rightMargin=0.3 * inch,
        title=f"Supplier Ledger - {supplier.name}",
    )
    styles = getSampleStyleSheet()
    elements = []

    brand_red = colors.HexColor("#B21E22")
    header_bg = colors.HexColor("#1A1A1A")
    alt_row = colors.HexColor("#F3F4F6")
    border = colors.HexColor("#9CA3AF")
    muted = colors.HexColor("#6B7280")
    pay_bg = colors.HexColor("#F0FDF4")

    title_style = ParagraphStyle("Title", parent=styles["Normal"], fontSize=14,
                                 fontName="Helvetica-Bold", alignment=TA_CENTER, spaceAfter=4)
    meta_style = ParagraphStyle("Meta", parent=styles["Normal"], fontSize=8,
                                fontName="Helvetica", alignment=TA_RIGHT)
    bar_style = ParagraphStyle("Bar", parent=styles["Normal"], fontSize=10,
                               fontName="Helvetica-Bold", textColor=colors.white)
    cell = ParagraphStyle("Cell", parent=styles["Normal"], fontSize=7, leading=9, fontName="Helvetica")
    cell_bold = ParagraphStyle("CellBold", parent=cell, fontName="Helvetica-Bold")
    cell_right = ParagraphStyle("CellRight", parent=cell, alignment=TA_RIGHT)
    cell_right_bold = ParagraphStyle("CellRightBold", parent=cell_right, fontName="Helvetica-Bold")
    head = ParagraphStyle("Head", parent=cell, fontName="Helvetica-Bold", textColor=colors.white)
    head_right = ParagraphStyle("HeadRight", parent=head, alignment=TA_RIGHT)
    section = ParagraphStyle("Section", parent=styles["Normal"], fontSize=10,
                             fontName="Helvetica-Bold", spaceBefore=4, spaceAfter=4)
    summary_label = ParagraphStyle("SumLabel", parent=cell, textColor=muted, fontSize=7)
    summary_value = ParagraphStyle("SumValue", parent=cell, fontName="Helvetica-Bold", fontSize=10, leading=12)

    date_from, date_to = report["date_from"], report["date_to"]
    if date_from and date_to:
        range_text = f"Dated: {date_from.strftime('%d-%b-%y')} TO {date_to.strftime('%d-%b-%y')}"
    elif date_from:
        range_text = f"From: {date_from.strftime('%d-%b-%y')}"
    elif date_to:
        range_text = f"Up to: {date_to.strftime('%d-%b-%y')}"
    else:
        range_text = "Complete ledger from start"

    elements.append(Paragraph("SUPPLIER LEDGER REPORT", title_style))
    elements.append(Paragraph(
        f"Printed On: {datetime.now(PKT).strftime('%A %d/%m/%Y %H:%M')}<br/>{range_text}", meta_style))
    elements.append(Spacer(1, 6))

    closing_text = format_supplier_balance(report["closing_balance"], report["closing_type"])
    bar = Table(
        [[
            Paragraph(f"{supplier.id:08d} :===> {escape(supplier.name.upper())}", bar_style),
            Paragraph(f"{balance_label(report['closing_type'])}: {closing_text}",
                      ParagraphStyle("BarBal", parent=bar_style, alignment=TA_RIGHT)),
        ]],
        colWidths=[8.4 * inch, 2.6 * inch],
    )
    bar.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), header_bg),
        ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("LEFTPADDING", (0, 0), (-1, -1), 8), ("RIGHTPADDING", (0, 0), (-1, -1), 8),
    ]))
    elements.append(bar)
    elements.append(Spacer(1, 6))

    # Summary strip
    tiles = [
        ("TOTAL PURCHASED", _format_money(report["total_credit"], blank_if_zero=False),
         f"{report['purchase_count']} purchase(s)"),
        ("TOTAL PAID", _format_money(report["total_debit"], blank_if_zero=False),
         f"{report['payment_count']} payment(s)"),
        ("OPENING BALANCE", format_supplier_balance(report["opening_balance"], report["opening_type"]),
         "before this period")
        if (date_from or date_to)
        else ("PAYMENTS WITH PROOF", f"{report['proof_count']} of {report['payment_count']}",
              "payment screenshots on file"),
        (balance_label(report["closing_type"]).upper(), closing_text, "closing balance"),
    ]
    summary = Table(
        [[Paragraph(f"{label}", summary_label) for label, _v, _n in tiles],
         [Paragraph(value, summary_value) for _l, value, _n in tiles],
         [Paragraph(note, summary_label) for _l, _v, note in tiles]],
        colWidths=[2.75 * inch] * 4,
    )
    summary.setStyle(TableStyle([
        ("BOX", (0, 0), (-1, -1), 0.6, border),
        ("LINEAFTER", (0, 0), (-2, -1), 0.4, border),
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F9FAFB")),
        ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
    ]))
    elements.append(summary)
    elements.append(Spacer(1, 8))

    # Main ledger table
    data = [[
        Paragraph("Date", head), Paragraph("Type", head), Paragraph("No", head), Paragraph("E-#", head),
        Paragraph("Narration", head), Paragraph("DEBIT (PAID)", head_right),
        Paragraph("CREDIT (PURCHASE)", head_right), Paragraph("BALANCE", head_right),
    ]]
    extra_styles = []
    for index, row in enumerate(report["rows"], start=1):
        data.append([
            Paragraph(row["date"].strftime("%d-%b-%y"), cell),
            Paragraph(row["type"] or "—", cell),
            Paragraph(escape(row["number"] or "—"), cell),
            Paragraph(escape(row["extra"] or "—"), cell),
            Paragraph(escape(row["narration"]), cell_bold if row["is_opening"] else cell),
            Paragraph(_format_money(row["debit"]), cell_right),
            Paragraph(_format_money(row["credit"]), cell_right),
            Paragraph(format_supplier_balance(row["balance"], row["balance_type"]), cell_right_bold),
        ])
        if row.get("is_payment"):
            extra_styles.append(("BACKGROUND", (0, index), (-1, index), pay_bg))
    data.append([
        "", "", "", "", Paragraph("Totals", cell_bold),
        Paragraph(_format_money(report["total_debit"], blank_if_zero=False), cell_right_bold),
        Paragraph(_format_money(report["total_credit"], blank_if_zero=False), cell_right_bold),
        Paragraph(closing_text, cell_right_bold),
    ])
    col_widths = [0.75 * inch, 0.45 * inch, 0.85 * inch, 0.55 * inch, 5.25 * inch,
                  1.0 * inch, 1.15 * inch, 1.0 * inch]
    table = Table(data, colWidths=col_widths, repeatRows=1)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), brand_red),
        ("BOX", (0, 0), (-1, -1), 1, border),
        ("INNERGRID", (0, 0), (-1, -1), 0.25, border),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("BACKGROUND", (0, -1), (-1, -1), alt_row),
        *extra_styles,
    ]))
    elements.append(table)

    # Payment details
    payments = report["payments"]
    elements.append(Spacer(1, 10))
    pay_rows = [[
        Paragraph("#", head), Paragraph("Date", head), Paragraph("Reference", head),
        Paragraph("Paid From", head), Paragraph("Note", head), Paragraph("Proof", head),
        Paragraph("Amount Paid", head_right), Paragraph("Balance After", head_right),
    ]]
    for number, row in enumerate(payments, start=1):
        details = row["payment"]
        pay_rows.append([
            Paragraph(str(number), cell),
            Paragraph(row["date"].strftime("%d-%b-%y"), cell),
            Paragraph(escape(" · ".join(x for x in (row["number"], details["reference"]) if x)), cell),
            Paragraph(escape(details["bank_name"] or details["method"]), cell),
            Paragraph(escape(details["note"] or "—"), cell),
            Paragraph("Screenshot on file" if details["proof"] else "Not uploaded", cell),
            Paragraph(_format_money(row["debit"], blank_if_zero=False), cell_right_bold),
            Paragraph(format_supplier_balance(row["balance"], row["balance_type"]), cell_right),
        ])
    if payments:
        pay_rows.append([
            "", "", "", "", "", Paragraph("Total paid", cell_bold),
            Paragraph(_format_money(report["total_debit"], blank_if_zero=False), cell_right_bold), "",
        ])
    else:
        pay_rows.append([Paragraph("No payments in this period.", cell)] + [""] * 7)
    pay_table = Table(
        pay_rows,
        colWidths=[0.3 * inch, 0.7 * inch, 1.75 * inch, 1.3 * inch, 3.5 * inch, 1.15 * inch, 1.2 * inch, 1.1 * inch],
        repeatRows=1,
    )
    pay_style = [
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#166534")),
        ("BOX", (0, 0), (-1, -1), 1, border),
        ("INNERGRID", (0, 0), (-1, -1), 0.25, border),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("BACKGROUND", (0, -1), (-1, -1), alt_row),
    ]
    if not payments:
        pay_style.append(("SPAN", (0, 1), (-1, 1)))
    pay_table.setStyle(TableStyle(pay_style))
    elements.append(KeepTogether([Paragraph(f"Payment Details ({len(payments)})", section), pay_table])
                    if len(payments) < 12 else Paragraph(f"Payment Details ({len(payments)})", section))
    if len(payments) >= 12:
        elements.append(pay_table)

    elements.append(Spacer(1, 8))
    elements.append(Paragraph(
        "P10 = purchase from supplier (credit) · BP = payment from bank ledger · PY = manual payment entry · "
        "Balance in brackets = advance paid to supplier",
        ParagraphStyle("Legend", parent=styles["Normal"], fontSize=7, alignment=TA_CENTER, textColor=muted)))
    elements.append(Spacer(1, 4))
    elements.append(Paragraph(
        "Sami Hamas Traders · RN COLOUR Accounts",
        ParagraphStyle("Foot", parent=styles["Normal"], fontSize=8, alignment=TA_CENTER, textColor=muted)))

    doc.build(elements)
    buffer.seek(0)
    return buffer
