"""Ink Hall Stock: the grouped stock summary used by the page, and its printable PDF.

Live stock comes from ink_inventory.calculate_live_stock (opening cans + Cans In - Cans Out).
Status per ink: Out of stock at 0 cans or less; Low at or below the ink's low_stock_threshold
(1 can when no threshold is set); otherwise In stock.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from io import BytesIO
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.pdfgen.canvas import Canvas
from reportlab.platypus import KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

PAKISTAN_TZ = timezone(timedelta(hours=5))
DEFAULT_LOW_CANS = 1

COMPANY_COLORS = ["#B21E22", "#0F2744", "#0F766E", "#7C3AED", "#B45309", "#1D4ED8"]

STATUS_LABELS = {"out": "Out of stock", "low": "Low", "ok": "In stock"}

# Checked in order: the first word found in the colour name wins ("R-Media Black" is black,
# "Honey Red" is honey red, not red).
_SWATCH_WORDS = [
    ("black", "black"),
    ("r-media", "media"),
    ("r media", "media"),
    ("medium", "media"),
    ("media", "media"),
    ("varnish", "media"),
    ("extender", "media"),
    ("white", "white"),
    ("magenta", "magenta"),
    ("cyan", "cyan"),
    ("honey", "honey"),
    ("d. red", "dred"),
    ("d.red", "dred"),
    ("dark red", "dred"),
    ("maroon", "dred"),
    ("red", "red"),
    ("yellow", "yellow"),
    ("gold", "gold"),
    ("silver", "silver"),
    ("parrot", "parrot"),
    ("light green", "lgreen"),
    ("l. green", "lgreen"),
    ("green", "green"),
    ("orange", "orange"),
    ("pink", "pink"),
    ("rubine", "magenta"),
    ("violet", "violet"),
    ("purple", "violet"),
    ("navy", "navy"),
    ("blue", "blue"),
    ("brown", "brown"),
]


def swatch_key(colour_name: str | None) -> str:
    """A CSS swatch key for a colour name ('Parrot Green' -> 'parrot'); 'none' when unknown."""
    name = re.sub(r"\s+", " ", (colour_name or "").strip().lower())
    for word, key in _SWATCH_WORDS:
        if word in name:
            return key
    return "none"


def fmt_qty(value, digits: int = 2) -> str:
    """4.0 -> '4', 1.5 -> '1.5', 1425 -> '1,425'."""
    amount = float(value or 0)
    if abs(amount - round(amount)) < 0.005:
        return f"{int(round(amount)):,}"
    return f"{amount:,.{digits}f}".rstrip("0").rstrip(".")


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-") or "other"


def _code_parts(code: str | None) -> tuple[str, str]:
    """'ALAM 0140' -> ('ALAM', '0140'); '3115' -> ('', '3115')."""
    code = (code or "").strip()
    match = re.match(r"^([A-Za-z]+)[\s\-]*(.*)$", code)
    if match:
        return match.group(1).upper(), match.group(2).strip()
    return "", code


def _code_sort_key(code: str | None) -> tuple:
    series, rest = _code_parts(code)
    number = re.match(r"\d+", rest or "")
    return (
        0 if code else 1,
        series,
        int(number.group(0)) if number else 10**9,
        (rest or "").lower(),
    )


def ink_status(current_cans: float, threshold) -> str:
    if current_cans <= 0.0001:
        return "out"
    limit = DEFAULT_LOW_CANS if threshold is None else float(threshold)
    if current_cans <= limit + 0.0001:
        return "low"
    return "ok"


def summarize_ink_stock(live_stock: list[dict]) -> dict:
    """Group live stock by ink company for the Ink Hall Stock page and PDF."""
    groups: dict[str, dict] = {}
    series_counts: dict[str, int] = {}
    for item in live_stock:
        ink = item["ink"]
        company = (ink.ink_company or "").strip() or "Other"
        key = _slug(company)
        group = groups.setdefault(
            key, {"key": key, "name": company, "rows": [], "cans": 0.0, "kg": 0.0}
        )
        series, _ = _code_parts(ink.color_code)
        if series:
            series_counts[series] = series_counts.get(series, 0) + 1
        cans = float(item["current_cans"])
        kg = float(item["current_weight"])
        status = ink_status(cans, ink.low_stock_threshold)
        group["rows"].append(
            {
                "id": ink.id,
                "ink": ink,
                "company": company,
                "company_key": key,
                "code": (ink.color_code or "").strip(),
                "series": series,
                "colour": (ink.name or "").strip(),
                "swatch": swatch_key(ink.name),
                "opening": float(item["initial_cans"]),
                "cans_in": float(item["cans_in"]),
                "cans_out": float(item["used"]),
                "cans": cans,
                "kg": kg,
                "weight_per_can": float(ink.weight_per_can or 0),
                "status": status,
                "status_label": STATUS_LABELS[status],
                "threshold": ink.low_stock_threshold,
            }
        )
        group["cans"] += cans
        group["kg"] += kg

    ordered = sorted(groups.values(), key=lambda g: (-len(g["rows"]), g["name"].lower()))
    for index, group in enumerate(ordered):
        group["color"] = COMPANY_COLORS[index % len(COMPANY_COLORS)]
        group["count"] = len(group["rows"])
        group["rows"].sort(key=lambda r: (_code_sort_key(r["code"]), r["colour"].lower()))
        group["low"] = sum(1 for r in group["rows"] if r["status"] != "ok")
        for position, row in enumerate(group["rows"]):
            row["order"] = position

    rows = [row for group in ordered for row in group["rows"]]
    out_count = sum(1 for r in rows if r["status"] == "out")
    low_count = sum(1 for r in rows if r["status"] == "low")
    return {
        "groups": ordered,
        "rows": rows,
        "count": len(rows),
        "total_cans": sum(r["cans"] for r in rows),
        "total_kg": sum(r["kg"] for r in rows),
        "out_count": out_count,
        "low_count": low_count,
        "attention_count": out_count + low_count,
        "series": [
            {"key": key, "count": count}
            for key, count in sorted(series_counts.items(), key=lambda kv: (-kv[1], kv[0]))
        ],
    }


# --------------------------------------------------------------------------- PDF

PAGE_MARGIN = 0.55 * inch
FRAME_PAD = 6
FOOTER_Y = 0.3 * inch

BRAND = colors.HexColor("#1A1A1A")
BRAND_RED = colors.HexColor("#B21E22")
MUTED = colors.HexColor("#4B5563")
BORDER = colors.HexColor("#D1D5DB")
ALT_ROW = colors.HexColor("#F3F4F6")
TOTAL_BG = colors.HexColor("#E5E7EB")
OUT_RED = colors.HexColor("#B91C1C")
LOW_AMBER = colors.HexColor("#B45309")
OK_GREEN = colors.HexColor("#166534")
FOOTER_TEXT = "RN COLOUR · Ink"


def _t(value) -> str:
    return escape(str(value if value is not None else ""))


def _footer_canvas(title: str, printed: str):
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


def _base_style(header_rows: int = 1) -> list:
    return [
        ("BOX", (0, 0), (-1, -1), 0.4, BORDER),
        ("INNERGRID", (0, header_rows - 1), (-1, -1), 0.25, BORDER),
        ("BACKGROUND", (0, header_rows - 1), (-1, header_rows - 1), BRAND_RED),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 2.6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2.6),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
    ]


def generate_ink_stock_pdf(summary: dict, title: str = "Ink Hall Stock") -> BytesIO:
    buffer = BytesIO()
    printed = datetime.now(PAKISTAN_TZ).strftime("%d/%m/%Y %H:%M")
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        topMargin=0.45 * inch,
        bottomMargin=0.6 * inch,
        leftMargin=PAGE_MARGIN,
        rightMargin=PAGE_MARGIN,
        title=title,
        author="RN COLOUR",
    )
    avail = doc.width - 2 * FRAME_PAD
    styles = getSampleStyleSheet()

    title_style = ParagraphStyle(
        "InkTitle", parent=styles["Normal"], fontName="Helvetica-Bold", fontSize=16,
        leading=19, alignment=TA_CENTER, textColor=BRAND, spaceAfter=1,
    )
    sub_style = ParagraphStyle(
        "InkSub", parent=styles["Normal"], fontName="Helvetica", fontSize=9,
        alignment=TA_CENTER, textColor=MUTED, spaceAfter=10,
    )
    group_style = ParagraphStyle(
        "InkGroup", parent=styles["Normal"], fontName="Helvetica-Bold", fontSize=10,
        leading=12, textColor=colors.white,
    )
    group_meta = ParagraphStyle(
        "InkGroupMeta", parent=group_style, fontName="Helvetica", fontSize=8, alignment=TA_RIGHT,
    )
    cell = ParagraphStyle(
        "InkCell", parent=styles["Normal"], fontName="Helvetica", fontSize=8.5, leading=10.5,
        textColor=BRAND,
    )
    cell_right = ParagraphStyle("InkRight", parent=cell, alignment=TA_RIGHT)
    cell_center = ParagraphStyle("InkCenter", parent=cell, alignment=TA_CENTER)
    head = ParagraphStyle(
        "InkHead", parent=cell, fontName="Helvetica-Bold", textColor=colors.white,
        alignment=TA_CENTER,
    )
    head_left = ParagraphStyle("InkHeadLeft", parent=head, alignment=TA_LEFT)
    head_right = ParagraphStyle("InkHeadRight", parent=head, alignment=TA_RIGHT)
    bold = ParagraphStyle("InkBold", parent=cell, fontName="Helvetica-Bold")
    bold_right = ParagraphStyle("InkBoldRight", parent=cell_right, fontName="Helvetica-Bold")
    kpi_value = ParagraphStyle(
        "InkKpi", parent=cell, fontName="Helvetica-Bold", fontSize=14, leading=17,
        alignment=TA_CENTER,
    )
    kpi_label = ParagraphStyle(
        "InkKpiLabel", parent=cell, fontSize=7.5, leading=9, alignment=TA_CENTER, textColor=MUTED,
    )

    def status_para(row) -> Paragraph:
        colour = {"out": OUT_RED, "low": LOW_AMBER}.get(row["status"], OK_GREEN)
        weight = "Helvetica-Bold" if row["status"] != "ok" else "Helvetica"
        return Paragraph(
            f'<font name="{weight}" color="{colour.hexval()}">{_t(row["status_label"])}</font>',
            cell_center,
        )

    groups = summary["groups"]
    elements = [
        Paragraph(_t(title), title_style),
        Paragraph(f"RN COLOUR · Ink in the hall · Printed {printed}", sub_style),
    ]

    if not summary["rows"]:
        elements.append(Paragraph("No inks listed.", cell))
        doc.build(elements, canvasmaker=_footer_canvas(title, printed))
        buffer.seek(0)
        return buffer

    # ------------------------------------------------------------------ summary boxes
    kpi_w = avail / 4
    kpis = Table(
        [
            [
                Paragraph(fmt_qty(summary["total_cans"]), kpi_value),
                Paragraph(fmt_qty(summary["total_kg"]), kpi_value),
                Paragraph(f"{summary['count']:,}", kpi_value),
                Paragraph(f"{summary['attention_count']:,}", kpi_value),
            ],
            [
                Paragraph("Total cans", kpi_label),
                Paragraph("Total KG", kpi_label),
                Paragraph("Inks", kpi_label),
                Paragraph("Low / out of stock", kpi_label),
            ],
        ],
        colWidths=[kpi_w] * 4,
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

    # ------------------------------------------------------------------ stock by company
    elements.append(Spacer(1, 4))
    headers = ["Code", "Colour", "Cans", "KG", "Status"]
    code_w, cans_w, kg_w, status_w = 1.25 * inch, 0.8 * inch, 0.95 * inch, 1.1 * inch
    widths = [code_w, avail - code_w - cans_w - kg_w - status_w, cans_w, kg_w, status_w]
    for group in groups:
        rows = group["rows"]
        meta = (
            f"{group['count']} ink{'s' if group['count'] != 1 else ''} · "
            f"{fmt_qty(group['cans'])} cans · {fmt_qty(group['kg'])} kg"
        )
        data = [
            [Paragraph(_t(group["name"]), group_style), "", Paragraph(meta, group_meta), "", ""],
            [
                Paragraph(headers[0], head_left),
                Paragraph(headers[1], head_left),
                Paragraph(headers[2], head_right),
                Paragraph(headers[3], head_right),
                Paragraph(headers[4], head),
            ],
        ]
        for row in rows:
            data.append(
                [
                    Paragraph(_t(row["code"] or "—"), bold),
                    Paragraph(_t(row["colour"]), cell),
                    Paragraph(fmt_qty(row["cans"]), cell_right),
                    Paragraph(fmt_qty(row["kg"]), cell_right),
                    status_para(row),
                ]
            )
        data.append(
            [
                Paragraph("Total", bold),
                "",
                Paragraph(fmt_qty(group["cans"]), bold_right),
                Paragraph(fmt_qty(group["kg"]), bold_right),
                "",
            ]
        )
        table = Table(data, colWidths=widths, hAlign="LEFT", repeatRows=2, rowSplitRange=(4, -3))
        style = _base_style(header_rows=2) + [
            ("SPAN", (0, 0), (1, 0)),
            ("SPAN", (2, 0), (4, 0)),
            ("BACKGROUND", (0, 0), (-1, 0), BRAND),
            ("TOPPADDING", (0, 0), (-1, 0), 4.5),
            ("BOTTOMPADDING", (0, 0), (-1, 0), 4.5),
            ("SPAN", (0, -1), (1, -1)),
            ("BACKGROUND", (0, -1), (-1, -1), TOTAL_BG),
        ]
        if len(rows) > 1:
            style.append(("ROWBACKGROUNDS", (0, 2), (-1, -2), [colors.white, ALT_ROW]))
        table.setStyle(TableStyle(style))
        elements.append(Spacer(1, 8))
        elements.append(KeepTogether([table]) if len(rows) <= 10 else table)

    grand = Table(
        [[
            Paragraph("Grand total", bold),
            Paragraph(f"{summary['count']:,} inks", bold_right),
            Paragraph(f"{fmt_qty(summary['total_cans'])} cans", bold_right),
            Paragraph(f"{fmt_qty(summary['total_kg'])} kg", bold_right),
        ]],
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
    note = ParagraphStyle("InkNote", parent=cell, fontSize=7, leading=9, textColor=MUTED, spaceBefore=6)
    elements.append(
        Paragraph(
            "Stock = opening cans + Cans In - Cans Out. Low = at or below the ink's low-stock "
            "level (1 can when none is set).",
            note,
        )
    )

    doc.build(elements, canvasmaker=_footer_canvas(title, printed))
    buffer.seek(0)
    return buffer
