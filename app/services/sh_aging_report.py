"""Customer invoice aging report — days outstanding, recalculated on each run."""

from datetime import date, datetime
from io import BytesIO

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from app.models import ShClientCompany, ShSaleInvoice
from app.services.sh_bank import filter_by_bank
from app.services.sh_ledger_sync import compute_invoice_balances

BUCKETS = (
    ("first_15", "First 15 days", 0, 15),
    ("d16_30", "16 to 30 days", 16, 30),
    ("d31_45", "31 to 45 days", 31, 45),
    ("d46_60", "46 to 60 days", 46, 60),
    ("d61_75", "61 to 75 days", 61, 75),
    ("d76_90", "76 to 90 days", 76, 90),
    ("d91_120", "91 to 120 days", 91, 120),
    ("above_120", "above 120 days", 121, None),
)


def _format_money(value: float) -> str:
    amount = float(value or 0)
    if amount.is_integer():
        return f"{int(amount):,}"
    return f"{amount:,.2f}"


def _material_label(line) -> str:
    parts = [line.item_name or "Material"]
    if line.size:
        parts.append(str(line.size))
    if line.net_weight:
        parts.append(f"{float(line.net_weight):,.3f} kg")
    return " · ".join(parts)


def _bucket_key(aging_days: int) -> str:
    for key, _label, start, end in BUCKETS:
        if end is None and aging_days >= start:
            return key
        if end is not None and start <= aging_days <= end:
            return key
    return "above_120"


def _split_remaining(lines, remaining: float) -> list[float]:
    """Split an invoice's exact remaining across its lines. Last line absorbs rounding."""
    if not lines:
        return [round(remaining, 2)]
    gross = sum(float(line.line_total or 0) for line in lines)
    if gross <= 0:
        amounts = [0.0] * len(lines)
        amounts[-1] = round(remaining, 2)
        return amounts
    amounts = []
    used = 0.0
    for index, line in enumerate(lines):
        if index == len(lines) - 1:
            amounts.append(round(remaining - used, 2))
        else:
            share = round(float(line.line_total or 0) / gross * remaining, 2)
            amounts.append(share)
            used += share
    return amounts


def build_customer_aging_report(client_id: int, as_on: date) -> dict:
    client = ShClientCompany.query.get_or_404(client_id)
    balances = compute_invoice_balances(client_id)
    invoices = (
        filter_by_bank(ShSaleInvoice.query, ShSaleInvoice)
        .filter(
            ShSaleInvoice.sold_to_client_id == client_id,
            ShSaleInvoice.invoice_date <= as_on,
        )
        .order_by(ShSaleInvoice.invoice_date.asc(), ShSaleInvoice.id.asc())
        .all()
    )

    rows = []
    running = 0.0
    buckets = {key: 0.0 for key, *_rest in BUCKETS}

    for invoice in invoices:
        total = float(invoice.total_amount or 0)
        remaining = round(
            float(balances.get(invoice.id, {}).get("remaining", total)),
            2,
        )
        if remaining <= 0.01:
            continue

        aging_days = (as_on - invoice.invoice_date).days
        bucket = _bucket_key(aging_days)
        lines = list(invoice.lines) if invoice.lines else []
        portions = _split_remaining(lines, remaining)
        materials = [_material_label(line) for line in lines] or [invoice.notes or "—"]

        for material, amount in zip(materials, portions):
            if amount <= 0:
                continue
            running = round(running + amount, 2)
            buckets[bucket] = round(buckets[bucket] + amount, 2)
            rows.append(
                {
                    "date": invoice.invoice_date,
                    "invoice_number": invoice.invoice_number,
                    "material": material,
                    "amount": amount,
                    "running_balance": running,
                    "aging_days": aging_days,
                }
            )

    return {
        "client": client,
        "as_on": as_on,
        "rows": rows,
        "total_balance": running,
        "buckets": buckets,
        "bucket_labels": [(key, label) for key, label, *_rest in BUCKETS],
    }


def generate_customer_aging_pdf(report: dict) -> BytesIO:
    client = report["client"]
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        topMargin=0.45 * inch,
        bottomMargin=0.45 * inch,
        leftMargin=0.45 * inch,
        rightMargin=0.45 * inch,
    )
    styles = getSampleStyleSheet()
    brand = colors.HexColor("#1A1A1A")
    header_bg = colors.HexColor("#F3F4F6")
    border = colors.HexColor("#111827")
    muted = colors.HexColor("#4B5563")

    title_style = ParagraphStyle(
        "AgingTitle",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=16,
        alignment=TA_CENTER,
        textColor=brand,
        spaceAfter=2,
    )
    sub_style = ParagraphStyle(
        "AgingSub",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=11,
        alignment=TA_CENTER,
        textColor=brand,
        spaceAfter=12,
    )
    meta_style = ParagraphStyle(
        "AgingMeta",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=10,
        textColor=brand,
        spaceAfter=8,
    )
    cell = ParagraphStyle(
        "AgingCell",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=8,
        leading=10,
        textColor=brand,
    )
    cell_right = ParagraphStyle("AgingRight", parent=cell, alignment=TA_RIGHT)
    head = ParagraphStyle(
        "AgingHead",
        parent=cell,
        fontName="Helvetica-Bold",
        alignment=TA_CENTER,
    )
    material_style = ParagraphStyle(
        "AgingMaterial",
        parent=cell,
        fontSize=7.5,
        leading=9,
    )

    elements = [
        Paragraph("Customer Aging Report", title_style),
        Paragraph(f"As On &nbsp;&nbsp; {report['as_on'].strftime('%d/%m/%Y')}", sub_style),
        Paragraph(f"Customer Name:&nbsp;&nbsp;&nbsp; {client.name.upper()}", meta_style),
    ]

    header = [
        Paragraph("Invoice Date", head),
        Paragraph("Invoice No", head),
        Paragraph("Material", head),
        Paragraph("Balance Left", head),
        Paragraph("Total Balance", head),
        Paragraph("Aging Days", head),
    ]
    data = [header]
    for row in report["rows"]:
        data.append(
            [
                Paragraph(row["date"].strftime("%d/%m/%Y"), cell),
                Paragraph(str(row["invoice_number"]), cell),
                Paragraph(row["material"], material_style),
                Paragraph(_format_money(row["amount"]), cell_right),
                Paragraph(_format_money(row["running_balance"]), cell_right),
                Paragraph(str(row["aging_days"]), ParagraphStyle("Days", parent=cell, alignment=TA_CENTER)),
            ]
        )
    if not report["rows"]:
        data.append(
            [Paragraph("No invoices on or before this date.", cell), "", "", "", "", ""]
        )

    table = Table(
        data,
        colWidths=[1.05 * inch, 0.85 * inch, 2.35 * inch, 1.15 * inch, 1.15 * inch, 0.85 * inch],
        repeatRows=1,
    )
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), header_bg),
                ("BOX", (0, 0), (-1, -1), 1, border),
                ("INNERGRID", (0, 0), (-1, -1), 0.4, border),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("ALIGN", (3, 1), (4, -1), "RIGHT"),
                ("ALIGN", (5, 1), (5, -1), "CENTER"),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    elements.append(table)
    elements.append(Spacer(1, 16))
    elements.append(
        Paragraph(
            f"Total Balance:&nbsp;&nbsp;&nbsp; <b>{_format_money(report['total_balance'])}</b>",
            ParagraphStyle(
                "TotalBal",
                parent=styles["Normal"],
                fontName="Helvetica-Bold",
                fontSize=12,
                alignment=TA_CENTER,
                spaceAfter=14,
            ),
        )
    )

    labels = report["bucket_labels"]
    amounts = report["buckets"]

    def bucket_table(slice_labels):
        head_row = [Paragraph(label, head) for _key, label in slice_labels]
        value_row = [
            Paragraph(_format_money(amounts[key]), ParagraphStyle("Bkt", parent=cell, alignment=TA_CENTER, fontName="Helvetica-Bold"))
            for key, _label in slice_labels
        ]
        width = 7.4 * inch / len(slice_labels)
        built = Table([head_row, value_row], colWidths=[width] * len(slice_labels))
        built.setStyle(
            TableStyle(
                [
                    ("BOX", (0, 0), (-1, -1), 1, border),
                    ("INNERGRID", (0, 0), (-1, -1), 0.4, border),
                    ("BACKGROUND", (0, 0), (-1, 0), header_bg),
                    ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                    ("TOPPADDING", (0, 0), (-1, -1), 6),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                ]
            )
        )
        return built

    elements.append(bucket_table(labels[:4]))
    elements.append(Spacer(1, 10))
    elements.append(bucket_table(labels[4:]))
    elements.append(Spacer(1, 14))
    elements.append(
        Paragraph(
            f"Sami Hamas Traders · Printed {datetime.now().strftime('%d-%m-%Y %H:%M')} · "
            "Aging days are counted from each invoice date up to the As On date.",
            ParagraphStyle(
                "Foot",
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
