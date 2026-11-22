"""Printable register of material purchases received into In Hall / Machine Hall."""

import re
from datetime import datetime, timedelta, timezone
from io import BytesIO
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen.canvas import Canvas
from reportlab.platypus import CondPageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from app.services.hall_stock import group_rolls

PAGE_MARGIN = 0.5 * inch
FRAME_PAD = 6  # SimpleDocTemplate's default frame padding
FOOTER_Y = 0.3 * inch
PAKISTAN_TZ = timezone(timedelta(hours=5))

BRAND = colors.HexColor("#1A1A1A")
BRAND_RED = colors.HexColor("#B21E22")
MUTED = colors.HexColor("#4B5563")
SOFT = "#6B7280"
BORDER = colors.HexColor("#D1D5DB")
ALT_ROW = colors.HexColor("#F3F4F6")
TOTAL_BG = colors.HexColor("#E5E7EB")
FOOTER_TEXT = "RN COLOUR · Printing Materials"
TITLE = "Material Purchases"

HALLS = (("in_hall", "In Hall"), ("machine_hall", "Machine Hall"))
PERIOD_LABELS = {
    "this_month": "This month",
    "last_month": "Last month",
    "today": "Today",
}

_NUMERIC = re.compile(r"^\d+(?:\.\d+)?(?:\s*-\s*\d+(?:\.\d+)?)?$")


# --------------------------------------------------------------------------- text helpers


def _t(value) -> str:
    """Escape user text for a Paragraph.

    The micro sign (U+00B5) has no glyph in the built-in fonts and prints blank, so it is
    swapped for the Greek mu (U+03BC), which reportlab draws from the Symbol font.
    """
    text = str(value if value is not None else "").replace("µ", "μ")
    return escape(text)


def _t_lines(value) -> str:
    """Escaped multi-line text (notes typed with line breaks keep them)."""
    lines = [line.strip() for line in str(value or "").splitlines()]
    return "<br/>".join(_t(line) for line in lines if line)


def _format_num(value, digits: int = 3) -> str:
    if value is None:
        return "—"
    amount = float(value)
    if amount == 0:
        return "0"
    return f"{amount:,.{digits}f}"


def _money(value) -> str:
    if value is None:
        return "—"
    return f"{float(value):,.2f}"


def _format_rolls(value) -> str:
    """Roll counts are whole numbers in practice: 6, not 6.00 (keeps a fraction if there is one)."""
    if value is None:
        return "—"
    amount = float(value)
    if abs(amount - round(amount)) < 0.005:
        return f"{round(amount):,}"
    return f"{amount:,.2f}"


def _weight(value: float) -> str:
    text = f"{float(value):,.3f}".rstrip("0").rstrip(".")
    return text or "0"


def _size_label(size) -> str:
    size = (size or "").strip()
    if not size:
        return ""
    return f"{size} mm" if _NUMERIC.match(size) else size


def _micron_label(micron) -> str:
    micron = (micron or "").strip()
    if not micron:
        return ""
    return f"{micron}μ" if _NUMERIC.match(micron) else micron


def _family_label(material_type, material_name) -> str:
    """'LD · Natural' (name · type, as on the hall pages); just 'PET' when both match."""
    name = (material_name or "").strip()
    kind = (material_type or "").strip()
    if not kind or kind.casefold() == name.casefold():
        return name or kind or "Other"
    if not name:
        return kind
    return f"{name} · {kind}"


def _day(value) -> str:
    """1 Nov 2026 (no leading zero on the day)."""
    return f"{value.day} {value.strftime('%b %Y')}"


def _period_text(filters: dict) -> str:
    period = filters.get("period") or "this_month"
    date_from, date_to = filters.get("date_from"), filters.get("date_to")
    if period == "all" or (not date_from and not date_to):
        return "All dates"
    if date_from and date_to:
        span = _day(date_from) if date_from == date_to else f"{_day(date_from)} – {_day(date_to)}"
    elif date_from:
        span = f"From {_day(date_from)}"
    else:
        span = f"Up to {_day(date_to)}"
    label = PERIOD_LABELS.get(period)
    return f"{label}: {span}" if label else span


def _filter_text(filters: dict) -> str:
    parts = [_t(_period_text(filters))]
    location = filters.get("location") or ""
    parts.append(dict(HALLS).get(location, "Both halls"))
    term = (filters.get("q") or "").strip()
    if term:
        parts.append(f"Search: “{_t(term)}”")
    return " · ".join(parts)


# --------------------------------------------------------------------------- page chrome


def _footer_canvas(title: str, printed: str, running: str):
    """Canvas that draws the footer ('Page N of M') and a small running header from page 2."""

    class FooterCanvas(Canvas):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self._saved_pages = []

        def showPage(self):
            self._saved_pages.append(dict(self.__dict__))
            self._startPage()

        def save(self):
            total = len(self._saved_pages)
            for state in self._saved_pages:
                self.__dict__.update(state)
                self._draw_chrome(total)
                super().showPage()
            super().save()

        def _draw_chrome(self, total: int):
            page_w, page_h = self._pagesize
            left = PAGE_MARGIN + FRAME_PAD
            right = page_w - PAGE_MARGIN - FRAME_PAD
            self.saveState()
            self.setStrokeColor(BORDER)
            self.setLineWidth(0.5)
            self.line(left, FOOTER_Y + 10, right, FOOTER_Y + 10)
            self.setFont("Helvetica", 7.5)
            self.setFillColor(MUTED)
            self.drawString(left, FOOTER_Y, FOOTER_TEXT)
            self.drawCentredString(page_w / 2, FOOTER_Y, f"{title} · Printed {printed}")
            self.drawRightString(right, FOOTER_Y, f"Page {self._pageNumber} of {total}")
            if self._pageNumber > 1:
                top = page_h - 0.3 * inch
                self.setFont("Helvetica-Bold", 8)
                self.setFillColor(BRAND_RED)
                self.drawString(left, top, title)
                title_w = self.stringWidth(title, "Helvetica-Bold", 8)
                self.setFont("Helvetica", 7.5)
                self.setFillColor(MUTED)
                self.drawString(left + title_w + 6, top, running)
                self.drawRightString(right, top, "RN COLOUR")
            self.restoreState()

    return FooterCanvas


# --------------------------------------------------------------------------- tables


def _base_style(header_rows: int = 1) -> list:
    return [
        ("BOX", (0, 0), (-1, -1), 0.4, BORDER),
        ("INNERGRID", (0, header_rows - 1), (-1, -1), 0.25, BORDER),
        ("BACKGROUND", (0, header_rows - 1), (-1, header_rows - 1), BRAND_RED),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
    ]


def generate_hall_purchases_pdf(purchases, summary: dict, filters: dict) -> BytesIO:
    buffer = BytesIO()
    printed = datetime.now(PAKISTAN_TZ).strftime("%d/%m/%Y %H:%M")
    filters = filters or {}
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        topMargin=0.45 * inch,
        bottomMargin=0.55 * inch,
        leftMargin=PAGE_MARGIN,
        rightMargin=PAGE_MARGIN,
        title=TITLE,
        author="RN COLOUR",
    )
    avail = doc.width - 2 * FRAME_PAD
    styles = getSampleStyleSheet()

    title_style = ParagraphStyle(
        "PurTitle", parent=styles["Normal"], fontName="Helvetica-Bold", fontSize=16,
        leading=19, alignment=TA_CENTER, textColor=BRAND, spaceAfter=1,
    )
    sub_style = ParagraphStyle(
        "PurSub", parent=styles["Normal"], fontName="Helvetica", fontSize=9,
        alignment=TA_CENTER, textColor=MUTED, spaceAfter=4,
    )
    filter_style = ParagraphStyle(
        "PurFilter", parent=styles["Normal"], fontName="Helvetica-Bold", fontSize=10,
        leading=13, alignment=TA_CENTER, textColor=BRAND, spaceAfter=10,
    )
    section_style = ParagraphStyle(
        "PurSection", parent=styles["Normal"], fontName="Helvetica-Bold", fontSize=12,
        leading=15, textColor=BRAND, spaceBefore=12, spaceAfter=6,
    )
    section_meta = f'<font name="Helvetica" size="8" color="{SOFT}">'
    cell = ParagraphStyle(
        "PurCell", parent=styles["Normal"], fontName="Helvetica", fontSize=7.5, leading=9.2,
        textColor=BRAND,
    )
    cell_right = ParagraphStyle("PurRight", parent=cell, alignment=TA_RIGHT)
    small = ParagraphStyle("PurSmall", parent=cell, fontSize=6.5, leading=8, textColor=MUTED)
    small_right = ParagraphStyle("PurSmallRight", parent=small, alignment=TA_RIGHT)
    note_style = ParagraphStyle(
        "PurNote", parent=cell, fontSize=7, leading=8.6, textColor=colors.HexColor("#374151"),
    )
    sum_cell = ParagraphStyle("PurSumCell", parent=cell, fontSize=8, leading=10)
    sum_right = ParagraphStyle("PurSumRight", parent=sum_cell, alignment=TA_RIGHT)
    head = ParagraphStyle(
        "PurHead", parent=cell, fontName="Helvetica-Bold", textColor=colors.white,
        alignment=TA_CENTER,
    )
    head_left = ParagraphStyle("PurHeadLeft", parent=head, alignment=TA_LEFT)
    head_right = ParagraphStyle("PurHeadRight", parent=head, alignment=TA_RIGHT)
    sum_head = ParagraphStyle("PurSumHead", parent=head_left, fontSize=8, leading=10)
    sum_head_right = ParagraphStyle("PurSumHeadRight", parent=sum_head, alignment=TA_RIGHT)
    bold = ParagraphStyle("PurBold", parent=cell, fontName="Helvetica-Bold")
    bold_right = ParagraphStyle("PurBoldRight", parent=cell_right, fontName="Helvetica-Bold")
    sum_bold = ParagraphStyle("PurSumBold", parent=sum_cell, fontName="Helvetica-Bold")
    sum_bold_right = ParagraphStyle("PurSumBoldRight", parent=sum_right, fontName="Helvetica-Bold")
    kpi_value = ParagraphStyle(
        "PurKpi", parent=cell, fontName="Helvetica-Bold", fontSize=14, leading=17,
        alignment=TA_CENTER,
    )
    kpi_label = ParagraphStyle(
        "PurKpiLabel", parent=cell, fontSize=7.5, leading=9, alignment=TA_CENTER, textColor=MUTED,
    )
    empty_style = ParagraphStyle(
        "PurEmpty", parent=styles["Normal"], fontName="Helvetica-Bold", fontSize=11,
        leading=14, alignment=TA_CENTER, textColor=MUTED,
    )

    filter_text = _filter_text(filters)
    running = _period_text(filters)
    location = filters.get("location") or ""
    elements = [
        Paragraph(TITLE, title_style),
        Paragraph(f"RN COLOUR · Printed {printed}", sub_style),
        Paragraph(filter_text, filter_style),
    ]

    # Oldest first: a printed register reads top to bottom in date order.
    rows = sorted(purchases or [], key=lambda p: (p.purchase_date, p.id or 0))

    if not rows:
        box = Table(
            [[Paragraph("No purchases in this period", empty_style)],
             [Paragraph("No purchase was recorded for the dates and filters above.",
                        ParagraphStyle("PurEmptySub", parent=kpi_label, fontSize=8.5, leading=11))]],
            colWidths=[avail],
            hAlign="LEFT",
        )
        box.setStyle(
            TableStyle(
                [
                    ("BOX", (0, 0), (-1, -1), 0.6, BORDER),
                    ("LINEABOVE", (0, 0), (-1, 0), 2, BRAND_RED),
                    ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#FAFAFA")),
                    ("TOPPADDING", (0, 0), (-1, 0), 14),
                    ("BOTTOMPADDING", (0, -1), (-1, -1), 14),
                ]
            )
        )
        elements.append(Spacer(1, 6))
        elements.append(box)
        doc.build(elements, canvasmaker=_footer_canvas(TITLE, printed, running))
        buffer.seek(0)
        return buffer

    total = summary["total"]
    any_priced = total["priced"] > 0
    any_rolls = any(p.rolls is not None for p in rows)

    def amount_cell(bucket, value_style, note_style):
        """Amount for a summary bucket, with 'N of M priced' when only some purchases have a price."""
        if not bucket["priced"]:
            return Paragraph("—", value_style)
        parts = [Paragraph(_money(bucket["amount"]), value_style)]
        if bucket["priced"] < bucket["count"]:
            parts.append(Paragraph(f"{bucket['priced']} of {bucket['count']} priced", note_style))
        return parts

    # ------------------------------------------------------------------ summary boxes
    kpi_cells = [
        (f"{total['count']:,}", "Purchases"),
        (_format_num(total["kg"], 3), "Total KG"),
        (_format_rolls(total["rolls"]) if any_rolls else "—", "Total rolls"),
    ]
    if any_priced:
        label = "Amount"
        if total["priced"] < total["count"]:
            label += f" · {total['priced']} of {total['count']} priced"
        kpi_cells.append((f'<font size="9">Rs</font> {_money(total["amount"])}', label))
    kpi_w = avail / len(kpi_cells)
    kpis = Table(
        [
            [Paragraph(value, kpi_value) for value, _ in kpi_cells],
            [Paragraph(label, kpi_label) for _, label in kpi_cells],
        ],
        colWidths=[kpi_w] * len(kpi_cells),
        hAlign="LEFT",
    )
    kpis.setStyle(
        TableStyle(
            [
                ("BOX", (0, 0), (-1, -1), 0.6, BORDER),
                ("LINEAFTER", (0, 0), (-2, -1), 0.4, BORDER),
                ("LINEABOVE", (0, 0), (-1, 0), 2, BRAND_RED),
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#FAFAFA")),
                ("TOPPADDING", (0, 0), (-1, 0), 6),
                ("BOTTOMPADDING", (0, 0), (-1, 0), 0),
                ("TOPPADDING", (0, 1), (-1, 1), 1),
                ("BOTTOMPADDING", (0, 1), (-1, 1), 6),
            ]
        )
    )
    elements.append(kpis)

    # ------------------------------------------------------------------ by hall / by supplier
    def summary_table(first_label: str, entries: list) -> Table:
        headers = [first_label, "Purchases", "KG", "Rolls"] + (["Amount (Rs)"] if any_priced else [])
        if any_priced:
            widths = [avail * 0.36, avail * 0.13, avail * 0.17, avail * 0.12, avail * 0.22]
        else:
            widths = [avail * 0.46, avail * 0.16, avail * 0.22, avail * 0.16]
        data = [[Paragraph(headers[0], sum_head)] + [Paragraph(h, sum_head_right) for h in headers[1:]]]
        for name_para, bucket in entries + [(Paragraph("Total", sum_bold), total)]:
            is_total = bucket is total
            value = sum_bold_right if is_total else sum_right
            line = [
                name_para,
                Paragraph(f"{bucket['count']:,}", value),
                Paragraph(_format_num(bucket["kg"], 3), value),
                Paragraph(_format_rolls(bucket["rolls"]) if any_rolls else "—", value),
            ]
            if any_priced:
                line.append(amount_cell(bucket, value, small_right))
            data.append(line)
        table = Table(data, colWidths=widths, hAlign="LEFT", repeatRows=1, rowSplitRange=(2, -2))
        style = _base_style()
        style += [
            ("TOPPADDING", (0, 1), (-1, -1), 2),
            ("BOTTOMPADDING", (0, 1), (-1, -1), 2),
            ("BACKGROUND", (0, -1), (-1, -1), TOTAL_BG),
        ]
        if len(data) > 3:
            style.append(("ROWBACKGROUNDS", (0, 1), (-1, -2), [colors.white, ALT_ROW]))
        table.setStyle(TableStyle(style))
        return table

    if not location:
        by_hall = summary.get("by_hall") or {}
        blank = {"count": 0, "kg": 0.0, "rolls": 0.0, "amount": 0.0, "priced": 0}
        hall_entries = [
            (Paragraph(label, sum_bold), by_hall.get(key, blank))
            for key, label in HALLS
        ]
        elements.append(CondPageBreak(1.3 * inch))
        elements.append(Paragraph("By hall", section_style))
        elements.append(summary_table("Hall", hall_entries))

    supplier_entries = []
    for name, bucket in summary.get("by_supplier") or []:
        if not name or name == "—":
            para = Paragraph(f'<font color="{SOFT}">No supplier entered</font>', sum_cell)
        else:
            para = Paragraph(_t(name), sum_bold)
        supplier_entries.append((para, bucket))
    count = len(supplier_entries)
    elements.append(CondPageBreak(1.3 * inch))
    elements.append(
        Paragraph(
            f"By supplier {section_meta}· {count} supplier{'s' if count != 1 else ''}, "
            f"most KG first</font>",
            section_style,
        )
    )
    elements.append(summary_table("Supplier", supplier_entries))

    # ------------------------------------------------------------------ purchase register
    pad = 3  # cell padding left / right in this table

    def fit(values, total_value=None, minimum=0.0):
        """Width that keeps every number in a column on one line (they must never wrap)."""
        size = cell.fontSize
        widest = max([stringWidth(v, "Helvetica", size) for v in values] + [minimum - 2 * pad - 1])
        if total_value is not None:
            widest = max(widest, stringWidth(total_value, "Helvetica-Bold", size))
        return widest + 2 * pad + 1

    # (label, width, header style, numeric). Notes (None) takes whatever width is left over.
    columns = [("Date", fit(["00/00/0000"], minimum=0.55 * inch), head_left, False)]
    if not location:
        columns.append(("Hall", 0.5 * inch, head_left, False))
    columns += [
        # Without the Hall column (one hall chosen) supplier and material get a little more room.
        ("Supplier / Bill no.", (1.15 if location else 0.98) * inch, head_left, False),
        ("Material", (1.25 if location else 1.08) * inch, head_left, False),
        ("KG", fit([_format_num(p.kg, 3) for p in rows], _format_num(total["kg"], 3),
                   0.58 * inch), head_right, True),
        ("Rolls", 0.74 * inch, head_right, True),
    ]
    if any_priced:
        columns += [
            ("Rate / kg", fit([_money(p.rate_per_kg) for p in rows], minimum=0.52 * inch),
             head_right, True),
            ("Amount (Rs)", fit([_money(p.amount) for p in rows], _money(total["amount"]),
                                0.74 * inch), head_right, True),
        ]
    columns.append(("Notes", None, head_left, False))
    fixed = sum(width for _, width, _, _ in columns if width)
    notes_w = max(0.9 * inch, avail - fixed)
    widths = [width or notes_w for _, width, _, _ in columns]
    if sum(widths) > avail:
        scale = avail / sum(widths)
        widths = [width * scale for width in widths]

    data = [[Paragraph(label, style) for label, _, style, _ in columns]]
    for p in rows:
        supplier = (
            Paragraph(_t(p.supplier_name), cell) if p.supplier_name
            else Paragraph(f'<font color="{SOFT}">—</font>', cell)
        )
        supplier_parts = [supplier]
        if p.bill_no:
            supplier_parts.append(Paragraph(f"Bill no. {_t(p.bill_no)}", small))

        spec = " · ".join(
            _t(part) for part in (_size_label(p.size), _micron_label(p.micron)) if part
        )
        material = [Paragraph(_t(_family_label(p.material_type, p.material_name)), bold)]
        if spec:
            material.append(Paragraph(spec, cell))

        rolls = [Paragraph(_format_rolls(p.rolls), cell_right)]
        weights = group_rolls(p.roll_list)
        if weights:
            rolls.append(
                Paragraph(", ".join(f"{_weight(w)}\u00a0×\u00a0{n}" for w, n in weights), small_right)
            )

        line = [Paragraph(p.purchase_date.strftime("%d/%m/%Y"), cell)]
        if not location:
            line.append(Paragraph(_t(p.location_label), cell))
        line += [
            supplier_parts,
            material,
            Paragraph(_format_num(p.kg, 3), cell_right),
            rolls,
        ]
        if any_priced:
            line += [
                Paragraph(_money(p.rate_per_kg), cell_right),
                Paragraph(_money(p.amount), cell_right),
            ]
        notes = _t_lines(p.notes)
        line.append(Paragraph(notes, note_style) if notes else Paragraph(f'<font color="{SOFT}">—</font>', cell))
        data.append(line)

    label_cols = 4 if not location else 3
    total_line = [
        Paragraph(f"Total · {total['count']:,} purchase{'s' if total['count'] != 1 else ''}", bold)
    ] + [""] * (label_cols - 1)
    total_line += [
        Paragraph(_format_num(total["kg"], 3), bold_right),
        Paragraph(_format_rolls(total["rolls"]) if any_rolls else "—", bold_right),
    ]
    if any_priced:
        total_line += ["", amount_cell(total, bold_right, small_right)]
    total_line.append("")
    data.append(total_line)

    count = len(rows)
    # Heading + column titles + a couple of rows, or start on a fresh page.
    elements.append(CondPageBreak(1.6 * inch))
    elements.append(
        Paragraph(
            f"Purchases {section_meta}· {count:,} record{'s' if count != 1 else ''}, "
            f"by date (oldest first)</font>",
            section_style,
        )
    )
    # A split keeps the header + 2 rows on the first page and never leaves the total alone.
    table = Table(data, colWidths=widths, hAlign="LEFT", repeatRows=1, rowSplitRange=(3, -2))
    style = _base_style()
    style += [
        ("LEFTPADDING", (0, 0), (-1, -1), pad),
        ("RIGHTPADDING", (0, 0), (-1, -1), pad),
        ("SPAN", (0, -1), (label_cols - 1, -1)),
        ("BACKGROUND", (0, -1), (-1, -1), TOTAL_BG),
        ("LINEABOVE", (0, -1), (-1, -1), 1.2, BRAND),
        ("TOPPADDING", (0, -1), (-1, -1), 5),
        ("BOTTOMPADDING", (0, -1), (-1, -1), 5),
    ]
    if len(data) > 3:
        style.append(("ROWBACKGROUNDS", (0, 1), (-1, -2), [colors.white, ALT_ROW]))
    table.setStyle(TableStyle(style))
    elements.append(table)

    doc.build(elements, canvasmaker=_footer_canvas(TITLE, printed, running))
    buffer.seek(0)
    return buffer
