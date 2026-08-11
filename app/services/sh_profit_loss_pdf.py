from datetime import datetime
from io import BytesIO
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_RIGHT
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from app.models import ShProfitLossRecord
from app.services.sh_profit_loss import get_broker_summaries, scoped_records_query

RN_LOGO_PATH = Path(__file__).resolve().parent.parent / "static" / "images" / "rn-colour-logo.png"
GREY_TEXT = colors.HexColor("#6B7280")
BRAND_RED = colors.HexColor("#B21E22")
BRAND_BLACK = colors.HexColor("#1A1A1A")
HEADER_BG = colors.HexColor("#FEF2F2")
HEADER_TEXT = colors.HexColor("#991B1B")
BORDER_GREY = colors.HexColor("#D1D5DB")
PROFIT_GREEN = colors.HexColor("#15803D")
LOSS_RED = colors.HexColor("#B91C1C")
ALT_ROW = colors.HexColor("#FAFAFA")


def _money(value: float) -> str:
    if float(value).is_integer():
        return f"{int(value):,}"
    return f"{value:,.2f}"


def _kg(value: float) -> str:
    if float(value).is_integer():
        return f"{int(value):,}"
    return f"{value:,.1f}"


def _logo_block():
    if RN_LOGO_PATH.exists():
        return Image(str(RN_LOGO_PATH), width=1.55 * inch, height=0.55 * inch, kind="proportional")
    return Paragraph(
        '<font color="#B21E22"><b>RN COLOUR</b></font>',
        ParagraphStyle("LogoText", fontSize=16, fontName="Helvetica-Bold"),
    )


def generate_profit_loss_record_pdf(record: ShProfitLossRecord) -> BytesIO:
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        topMargin=0.5 * inch,
        bottomMargin=0.5 * inch,
        leftMargin=0.6 * inch,
        rightMargin=0.6 * inch,
    )
    styles = getSampleStyleSheet()
    elements = [_logo_block(), Spacer(1, 10)]

    title_style = ParagraphStyle(
        "Title",
        parent=styles["Normal"],
        fontSize=16,
        textColor=BRAND_RED,
        fontName="Helvetica-Bold",
        spaceAfter=4,
    )
    subtitle_style = ParagraphStyle(
        "Subtitle",
        parent=styles["Normal"],
        fontSize=10,
        textColor=GREY_TEXT,
        spaceAfter=14,
    )
    label_style = ParagraphStyle(
        "Label",
        parent=styles["Normal"],
        fontSize=9,
        textColor=GREY_TEXT,
        fontName="Helvetica",
    )
    value_style = ParagraphStyle(
        "Value",
        parent=styles["Normal"],
        fontSize=11,
        textColor=BRAND_BLACK,
        fontName="Helvetica-Bold",
    )

    elements.append(Paragraph("Profit / Loss Record", title_style))
    elements.append(
        Paragraph(
            f"SH Traders · {record.record_date.strftime('%d %B %Y')} · {record.broker_label}",
            subtitle_style,
        )
    )

    rows = [
        ["Field", "Value"],
        ["Material Name", record.material_name],
        ["Size", record.size or "—"],
        ["Purchased By", record.broker_label],
        ["Quantity Purchased (KG)", _kg(record.purchase_kg)],
        ["Purchase Rate / KG", f"Rs {_money(record.purchase_rate_per_kg)}"],
        ["Purchase Total", f"Rs {_money(record.purchase_total)}"],
        ["Total Sold (KG)", _kg(record.sold_kg)],
        ["Sold Rate / KG", f"Rs {_money(record.sold_rate_per_kg)}"],
        ["Sold Total", f"Rs {_money(record.sold_total)}"],
        [
            "Profit",
            f"Rs {_money(record.profit_amount)}" if record.profit_amount else "—",
        ],
        [
            "Loss",
            f"Rs {_money(record.loss_amount)}" if record.loss_amount else "—",
        ],
    ]
    if record.notes:
        rows.append(["Notes", record.notes])

    table = Table(rows, colWidths=[2.3 * inch, 4.3 * inch])
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), BRAND_RED),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, 0), 10),
                ("BACKGROUND", (0, 1), (0, -1), HEADER_BG),
                ("TEXTCOLOR", (0, 1), (0, -1), HEADER_TEXT),
                ("FONTNAME", (0, 1), (0, -1), "Helvetica-Bold"),
                ("BOX", (0, 0), (-1, -1), 1, BORDER_GREY),
                ("INNERGRID", (0, 0), (-1, -1), 0.5, BORDER_GREY),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("TOPPADDING", (0, 0), (-1, -1), 8),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
                ("LEFTPADDING", (0, 0), (-1, -1), 10),
            ]
        )
    )
    elements.append(table)
    elements.append(Spacer(1, 18))

    result_color_hex = "#15803D" if record.profit_amount else "#B91C1C"
    result_text = (
        f"Net Profit: Rs {_money(record.profit_amount)}"
        if record.profit_amount
        else f"Net Loss: Rs {_money(record.loss_amount)}"
    )
    elements.append(
        Paragraph(
            f'<font color="{result_color_hex}"><b>{result_text}</b></font>',
            ParagraphStyle(
                "Result",
                parent=styles["Normal"],
                fontSize=13,
                alignment=TA_CENTER,
            ),
        )
    )
    elements.append(Spacer(1, 16))
    elements.append(
        Paragraph(
            f"Generated {datetime.now().strftime('%d-%m-%Y %H:%M')} · Record #{record.id}",
            ParagraphStyle(
                "Footer",
                parent=styles["Normal"],
                fontSize=8,
                textColor=GREY_TEXT,
                alignment=TA_CENTER,
            ),
        )
    )

    doc.build(elements)
    buffer.seek(0)
    return buffer


def generate_profit_loss_report_pdf(broker: str | None = None) -> BytesIO:
    query = scoped_records_query()
    if broker:
        query = query.filter(ShProfitLossRecord.broker == broker)
    records = query.all()
    summaries = get_broker_summaries(records)

    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=landscape(A4),
        topMargin=0.4 * inch,
        bottomMargin=0.4 * inch,
        leftMargin=0.35 * inch,
        rightMargin=0.35 * inch,
    )
    styles = getSampleStyleSheet()
    elements = []

    header = Table(
        [
            [
                _logo_block(),
                Paragraph(
                    "Profit / Loss Report<br/>"
                    f"<font size='9' color='#6B7280'>SH Traders · {datetime.now().strftime('%d %B %Y')}</font>",
                    ParagraphStyle(
                        "ReportTitle",
                        parent=styles["Normal"],
                        fontSize=14,
                        textColor=BRAND_RED,
                        fontName="Helvetica-Bold",
                        alignment=TA_RIGHT,
                        leading=18,
                    ),
                ),
            ]
        ],
        colWidths=[2.2 * inch, 8.5 * inch],
    )
    header.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "MIDDLE")]))
    elements.append(header)
    elements.append(Spacer(1, 10))

    summary_rows = [
        ["Broker", "Records", "Total Profit", "Total Loss", "Net"],
    ]
    grand_profit = grand_loss = 0.0
    for key in (ShProfitLossRecord.BROKER_HAMAS, ShProfitLossRecord.BROKER_AKRAM):
        bucket = summaries[key]
        net = bucket["profit"] - bucket["loss"]
        grand_profit += bucket["profit"]
        grand_loss += bucket["loss"]
        summary_rows.append(
            [
                bucket["label"],
                str(bucket["count"]),
                f"Rs {_money(bucket['profit'])}",
                f"Rs {_money(bucket['loss'])}",
                f"Rs {_money(net)}",
            ]
        )
    summary_rows.append(
        [
            "Grand Total",
            str(len(records)),
            f"Rs {_money(grand_profit)}",
            f"Rs {_money(grand_loss)}",
            f"Rs {_money(grand_profit - grand_loss)}",
        ]
    )

    summary_table = Table(summary_rows, colWidths=[2.8 * inch, 1.0 * inch, 1.6 * inch, 1.6 * inch, 1.6 * inch])
    summary_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), BRAND_RED),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("BACKGROUND", (0, -1), (-1, -1), HEADER_BG),
                ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
                ("BOX", (0, 0), (-1, -1), 1, BORDER_GREY),
                ("INNERGRID", (0, 0), (-1, -1), 0.5, BORDER_GREY),
                ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ]
        )
    )
    elements.append(summary_table)
    elements.append(Spacer(1, 12))

    cell = ParagraphStyle("Cell", parent=styles["Normal"], fontSize=7, leading=9)
    head = ParagraphStyle("Head", parent=cell, fontName="Helvetica-Bold", textColor=colors.white)

    data = [
        [
            Paragraph("Date", head),
            Paragraph("Material", head),
            Paragraph("Size", head),
            Paragraph("Pur. KG", head),
            Paragraph("Pur. Rate", head),
            Paragraph("Pur. Total", head),
            Paragraph("Sold KG", head),
            Paragraph("Sold Rate", head),
            Paragraph("Sold Total", head),
            Paragraph("Profit", head),
            Paragraph("Loss", head),
            Paragraph("Broker", head),
        ]
    ]
    for index, record in enumerate(records):
        data.append(
            [
                Paragraph(record.record_date.strftime("%d-%m-%Y"), cell),
                Paragraph(record.material_name, cell),
                Paragraph(record.size or "—", cell),
                Paragraph(_kg(record.purchase_kg), cell),
                Paragraph(_money(record.purchase_rate_per_kg), cell),
                Paragraph(_money(record.purchase_total), cell),
                Paragraph(_kg(record.sold_kg), cell),
                Paragraph(_money(record.sold_rate_per_kg), cell),
                Paragraph(_money(record.sold_total), cell),
                Paragraph(_money(record.profit_amount) if record.profit_amount else "—", cell),
                Paragraph(_money(record.loss_amount) if record.loss_amount else "—", cell),
                Paragraph(record.broker_label, cell),
            ]
        )

    if len(data) == 1:
        data.append([Paragraph("No records yet.", cell)] + [Paragraph("", cell)] * 11)

    col_widths = [0.72 * inch] * 12
    detail_table = Table(data, colWidths=col_widths, repeatRows=1)
    detail_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), BRAND_RED),
                ("BOX", (0, 0), (-1, -1), 1, BORDER_GREY),
                ("INNERGRID", (0, 0), (-1, -1), 0.25, BORDER_GREY),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, ALT_ROW]),
            ]
        )
    )
    elements.append(detail_table)

    doc.build(elements)
    buffer.seek(0)
    return buffer
