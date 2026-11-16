"""Printable stock lists for In Hall and Machine Hall."""

import re
from datetime import datetime
from io import BytesIO
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.pdfgen.canvas import Canvas
from reportlab.platypus import KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from app.models import HallStockItem, HallStockMovement
from app.services.hall_stock import group_rolls, load_rolls

PAGE_MARGIN = 0.5 * inch
FRAME_PAD = 6  # SimpleDocTemplate's default frame padding
FOOTER_Y = 0.3 * inch

BRAND = colors.HexColor("#1A1A1A")
BRAND_RED = colors.HexColor("#B21E22")
MUTED = colors.HexColor("#4B5563")
BORDER = colors.HexColor("#D1D5DB")
ALT_ROW = colors.HexColor("#F3F4F6")
TOTAL_BG = colors.HexColor("#E5E7EB")
WARN = colors.HexColor("#B45309")
FOOTER_TEXT = "RN COLOUR · Printing Materials"

_NUMERIC = re.compile(r"^\d+(?:\.\d+)?(?:\s*-\s*\d+(?:\.\d+)?)?$")


# --------------------------------------------------------------------------- text helpers


def _t(value) -> str:
    """Escape user text for a Paragraph.

    The micro sign (U+00B5) has no glyph in the built-in fonts and prints blank, so it is
    swapped for the Greek mu (U+03BC), which reportlab draws from the Symbol font.
    """
    text = str(value if value is not None else "").replace("\u00b5", "\u03bc")
    return escape(text)


def _format_num(value, digits: int = 3) -> str:
    if value is None:
        return "—"
    amount = float(value)
    if amount == 0:
        return "0"
    return f"{amount:,.{digits}f}"


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
        return "—"
    return f"{size} mm" if _NUMERIC.match(size) else size


def _micron_label(micron) -> str:
    micron = (micron or "").strip()
    if not micron:
        return "—"
    return f"{micron}\u03bc" if _NUMERIC.match(micron) else micron


def _family_label(material_type, material_name) -> str:
    """'LD · Natural' (name · type, as on the In Hall page); just 'PET' when both match."""
    name = (material_name or "").strip()
    kind = (material_type or "").strip()
    if not kind or kind.casefold() == name.casefold():
        return name or kind or "Other"
    if not name:
        return kind
    return f"{name} · {kind}"


def _sort_key(value) -> tuple:
    value = (value or "").strip()
    if not value:
        return (2, 0.0, "")
    match = re.match(r"^\d+(?:\.\d+)?", value)
    if match:
        return (0, float(match.group(0)), value.casefold())
    return (1, 0.0, value.casefold())


def _group_by_family(items):
    groups: dict[tuple, dict] = {}
    for item in items:
        kind = (item.material_type or "").strip()
        name = (item.material_name or "").strip()
        key = (kind.casefold(), name.casefold())
        label = _family_label(kind, name)
        bucket = groups.setdefault(key, {"label": label, "rows": [], "sort": (label.casefold(), key)})
        bucket["rows"].append(item)
    ordered = sorted(groups.values(), key=lambda group: group["sort"])
    for group in ordered:
        group["rows"].sort(key=lambda item: (_sort_key(item.size), _sort_key(item.micron)))
        group["kg"] = sum(float(item.kg or 0) for item in group["rows"])
        group["rolls"] = sum(float(item.rolls_left or 0) for item in group["rows"])
    return ordered


def _roll_breakdown(item: HallStockItem, cell, note) -> list:
    """Cell content for the roll weights column: '168.8 × 5, 30 × 1' plus any KG notes."""
    groups = item.roll_groups
    if not groups:
        return [Paragraph("—", cell)]
    text = ", ".join(f"{_weight(weight)} × {count}" for weight, count in groups)
    untracked = item.untracked_kg
    if untracked > 0:
        text += f' <font color="#6B7280">+ {_weight(untracked)} kg not split</font>'
    parts = [Paragraph(text, cell)]
    excess = item.roll_excess_kg
    if excess > 0:
        parts.append(Paragraph(f"Rolls exceed stock by {_weight(excess)} kg", note))
    return parts


# --------------------------------------------------------------------------- page chrome


def _footer_canvas(title: str, printed: str):
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
                self.setFont("Helvetica", 7.5)
                self.setFillColor(MUTED)
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


def generate_hall_stock_pdf(
    title: str,
    items: list[HallStockItem],
    movements: list[HallStockMovement],
    movement_title: str,
    *,
    include_where_used: bool = False,
) -> BytesIO:
    buffer = BytesIO()
    printed = datetime.now().strftime("%d/%m/%Y %H:%M")
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        topMargin=0.45 * inch,
        bottomMargin=0.55 * inch,
        leftMargin=PAGE_MARGIN,
        rightMargin=PAGE_MARGIN,
        title=title,
        author="RN COLOUR",
    )
    avail = doc.width - 2 * FRAME_PAD
    styles = getSampleStyleSheet()

    title_style = ParagraphStyle(
        "HallTitle", parent=styles["Normal"], fontName="Helvetica-Bold", fontSize=16,
        leading=19, alignment=TA_CENTER, textColor=BRAND, spaceAfter=1,
    )
    sub_style = ParagraphStyle(
        "HallSub", parent=styles["Normal"], fontName="Helvetica", fontSize=9,
        alignment=TA_CENTER, textColor=MUTED, spaceAfter=10,
    )
    section_style = ParagraphStyle(
        "HallSection", parent=styles["Normal"], fontName="Helvetica-Bold", fontSize=12,
        leading=15, textColor=BRAND, spaceBefore=12, spaceAfter=6,
    )
    group_style = ParagraphStyle(
        "HallGroup", parent=styles["Normal"], fontName="Helvetica-Bold", fontSize=10,
        leading=12, textColor=colors.white,
    )
    group_meta = ParagraphStyle(
        "HallGroupMeta", parent=group_style, fontName="Helvetica", fontSize=8, alignment=TA_RIGHT,
    )
    cell = ParagraphStyle(
        "HallCell", parent=styles["Normal"], fontName="Helvetica", fontSize=8, leading=10,
        textColor=BRAND,
    )
    cell_right = ParagraphStyle("HallRight", parent=cell, alignment=TA_RIGHT)
    note = ParagraphStyle("HallNote", parent=cell, fontSize=7, leading=8.5, textColor=WARN)
    head = ParagraphStyle(
        "HallHead", parent=cell, fontName="Helvetica-Bold", textColor=colors.white,
        alignment=TA_CENTER,
    )
    head_left = ParagraphStyle("HallHeadLeft", parent=head, alignment=TA_LEFT)
    bold = ParagraphStyle("HallBold", parent=cell, fontName="Helvetica-Bold")
    bold_right = ParagraphStyle("HallBoldRight", parent=cell_right, fontName="Helvetica-Bold")
    kpi_value = ParagraphStyle(
        "HallKpi", parent=cell, fontName="Helvetica-Bold", fontSize=14, leading=17,
        alignment=TA_CENTER,
    )
    kpi_label = ParagraphStyle(
        "HallKpiLabel", parent=cell, fontSize=7.5, leading=9, alignment=TA_CENTER, textColor=MUTED,
    )

    groups = _group_by_family(items)
    total_rolls = sum(group["rolls"] for group in groups)
    total_kg = sum(group["kg"] for group in groups)

    elements = [
        Paragraph(_t(title), title_style),
        Paragraph(f"RN COLOUR · Printed {printed}", sub_style),
    ]

    # ------------------------------------------------------------------ summary
    if items:
        kpi_w = avail / 3
        kpis = Table(
            [
                [Paragraph(_format_num(total_kg, 3), kpi_value),
                 Paragraph(_format_rolls(total_rolls), kpi_value),
                 Paragraph(f"{len(items):,}", kpi_value)],
                [Paragraph("Total KG", kpi_label),
                 Paragraph("Total rolls", kpi_label),
                 Paragraph("Stock rows", kpi_label)],
            ],
            colWidths=[kpi_w] * 3,
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
        elements.append(Spacer(1, 8))

        fam_headers = ["Material", "Rows", "Rolls", "KG", "Share of KG"]
        fam_widths = [avail * 0.40, avail * 0.12, avail * 0.12, avail * 0.18, avail * 0.18]
        fam_data = [[Paragraph(fam_headers[0], head_left)] + [Paragraph(h, head) for h in fam_headers[1:]]]
        for group in groups:
            share = (group["kg"] / total_kg * 100) if total_kg else 0
            fam_data.append(
                [
                    Paragraph(_t(group["label"]), bold),
                    Paragraph(f"{len(group['rows']):,}", cell_right),
                    Paragraph(_format_rolls(group["rolls"]), cell_right),
                    Paragraph(_format_num(group["kg"], 3), cell_right),
                    Paragraph(f"{share:.1f}%", cell_right),
                ]
            )
        fam_data.append(
            [
                Paragraph("Total", bold),
                Paragraph(f"{len(items):,}", bold_right),
                Paragraph(_format_rolls(total_rolls), bold_right),
                Paragraph(_format_num(total_kg, 3), bold_right),
                Paragraph("100.0%" if total_kg else "—", bold_right),
            ]
        )
        fam_table = Table(fam_data, colWidths=fam_widths, hAlign="LEFT", repeatRows=1)
        fam_style = _base_style()
        fam_style += [
            ("TOPPADDING", (0, 1), (-1, -1), 2),
            ("BOTTOMPADDING", (0, 1), (-1, -1), 2),
            ("BACKGROUND", (0, -1), (-1, -1), TOTAL_BG),
        ]
        if len(fam_data) > 3:
            fam_style.append(("ROWBACKGROUNDS", (0, 1), (-1, -2), [colors.white, ALT_ROW]))
        fam_table.setStyle(TableStyle(fam_style))
        elements.append(Paragraph("Summary by material", section_style))
        elements.append(fam_table)

    # ------------------------------------------------------------------ current stock
    elements.append(Paragraph("Current Stock", section_style))
    if not items:
        elements.append(Paragraph("No stock listed.", cell))
    else:
        stock_headers = ["Size", "Micron", "Rolls", "KG", "Rolls (weight × count)"]
        size_w, micron_w, rolls_w, kg_w = 0.95 * inch, 0.85 * inch, 0.6 * inch, 0.95 * inch
        widths = [size_w, micron_w, rolls_w, kg_w, avail - size_w - micron_w - rolls_w - kg_w]
        for group in groups:
            rows = group["rows"]
            meta = (
                f"{len(rows)} row{'s' if len(rows) != 1 else ''} · "
                f"{_format_rolls(group['rolls'])} rolls · {_format_num(group['kg'], 3)} kg"
            )
            data = [
                [Paragraph(_t(group["label"]), group_style), "", "", Paragraph(meta, group_meta), ""],
                [Paragraph(h, head) for h in stock_headers[:4]] + [Paragraph(stock_headers[4], head_left)],
            ]
            for item in rows:
                data.append(
                    [
                        Paragraph(_t(_size_label(item.size)), cell),
                        Paragraph(_t(_micron_label(item.micron)), cell),
                        Paragraph(_format_rolls(item.rolls_left), cell_right),
                        Paragraph(_format_num(item.kg, 3), cell_right),
                        _roll_breakdown(item, cell, note),
                    ]
                )
            data.append(
                [
                    Paragraph("Total", bold),
                    "",
                    Paragraph(_format_rolls(group["rolls"]), bold_right),
                    Paragraph(_format_num(group["kg"], 3), bold_right),
                    "",
                ]
            )
            # A split keeps at least 2 rows on each side (the total never sits alone),
            # and the group title + column headings repeat on the next page.
            table = Table(
                data, colWidths=widths, hAlign="LEFT", repeatRows=2, rowSplitRange=(4, -3)
            )
            style = _base_style(header_rows=2)
            style += [
                ("SPAN", (0, 0), (2, 0)),
                ("SPAN", (3, 0), (4, 0)),
                ("BACKGROUND", (0, 0), (-1, 0), BRAND),
                ("TOPPADDING", (0, 0), (-1, 0), 4),
                ("BOTTOMPADDING", (0, 0), (-1, 0), 4),
                ("LEFTPADDING", (0, 0), (-1, 0), 6),
                ("RIGHTPADDING", (0, 0), (-1, 0), 6),
                ("SPAN", (0, -1), (1, -1)),
                ("BACKGROUND", (0, -1), (-1, -1), TOTAL_BG),
            ]
            if len(rows) > 1:
                style.append(("ROWBACKGROUNDS", (0, 2), (-1, -2), [colors.white, ALT_ROW]))
            table.setStyle(TableStyle(style))
            elements.append(Spacer(1, 8))
            # Small groups stay on one page.
            elements.append(KeepTogether([table]) if len(rows) <= 8 else table)

        grand = Table(
            [[Paragraph("Grand total", bold),
              Paragraph(f"{len(items):,} rows", bold_right),
              Paragraph(f"{_format_rolls(total_rolls)} rolls", bold_right),
              Paragraph(f"{_format_num(total_kg, 3)} kg", bold_right)]],
            colWidths=[avail * 0.4, avail * 0.2, avail * 0.2, avail * 0.2],
            hAlign="LEFT",
        )
        grand.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, -1), TOTAL_BG),
                    ("LINEABOVE", (0, 0), (-1, 0), 1.2, BRAND),
                    ("TOPPADDING", (0, 0), (-1, -1), 5),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                ]
            )
        )
        elements.append(Spacer(1, 10))
        elements.append(grand)

    # ------------------------------------------------------------------ movements
    elements.append(Paragraph(_t(movement_title), section_style))
    if not movements:
        elements.append(Paragraph("No records yet.", cell))
    else:
        moved_rolls = {row.id: load_rolls(row.roll_weights) for row in movements}
        show_picked = not include_where_used or any(moved_rolls.values())
        # Notes (None) takes whatever width is left over.
        columns = [
            ("Date", 0.72 * inch, head, False),
            ("Material", 1.15 * inch, head_left, False),
            ("Size", 0.65 * inch, head, False),
            ("Micron", 0.55 * inch, head, False),
            ("KG", 0.75 * inch, head, True),
            ("Rolls", 0.45 * inch, head, True),
        ]
        if show_picked:
            columns.append(("Rolls moved", 1.2 * inch, head_left, False))
        if include_where_used:
            columns.append(("Where used", 1.3 * inch, head_left, False))
        columns.append(("Notes", None, head_left, False))
        fixed = sum(width for _, width, _, _ in columns if width)
        notes_w = max(0.9 * inch, avail - fixed)
        widths = [width or notes_w for _, width, _, _ in columns]
        if sum(widths) > avail:
            scale = avail / sum(widths)
            widths = [width * scale for width in widths]

        data = [[Paragraph(label, style) for label, _, style, _ in columns]]
        total_moved = 0.0
        total_moved_rolls = 0.0
        for row in movements:
            total_moved += float(row.gross_kg or 0)
            total_moved_rolls += float(row.rolls or 0)
            line = [
                Paragraph(row.movement_date.strftime("%d/%m/%Y"), cell),
                Paragraph(_t(_family_label(row.material_type, row.material_name)), cell),
                Paragraph(_t(_size_label(row.size)), cell),
                Paragraph(_t(_micron_label(row.micron)), cell),
                Paragraph(_format_num(row.gross_kg, 3), cell_right),
                Paragraph(_format_rolls(row.rolls), cell_right),
            ]
            if show_picked:
                picked = group_rolls(moved_rolls[row.id])
                text = ", ".join(f"{_weight(w)} × {n}" for w, n in picked) if picked else "—"
                line.append(Paragraph(text, cell))
            if include_where_used:
                line.append(Paragraph(_t(row.where_used or "—"), cell))
            line.append(Paragraph(_t(row.notes or "—"), cell))
            data.append(line)
        any_rolls = any(row.rolls is not None for row in movements)
        total_line = [Paragraph("Total", bold), "", "", "",
                      Paragraph(_format_num(total_moved, 3), bold_right),
                      Paragraph(_format_rolls(total_moved_rolls) if any_rolls else "—", bold_right)]
        total_line += [""] * (len(columns) - len(total_line))
        data.append(total_line)

        table = Table(data, colWidths=widths, hAlign="LEFT", repeatRows=1)
        style = _base_style()
        style += [
            ("SPAN", (0, -1), (3, -1)),
            ("BACKGROUND", (0, -1), (-1, -1), TOTAL_BG),
        ]
        if len(data) > 3:
            style.append(("ROWBACKGROUNDS", (0, 1), (-1, -2), [colors.white, ALT_ROW]))
        table.setStyle(TableStyle(style))
        elements.append(table)
        if len(movements) >= 200:
            elements.append(Spacer(1, 3))
            elements.append(Paragraph("Showing the latest 200 records.", ParagraphStyle(
                "HallMoveNote", parent=cell, fontSize=7, textColor=MUTED)))

    doc.build(elements, canvasmaker=_footer_canvas(title, printed))
    buffer.seek(0)
    return buffer
