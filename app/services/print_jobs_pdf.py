"""Printable Job Planning documents: the Job Card and the Pending Jobs report.

Both are built with reportlab platypus in the house style used by the other RN COLOUR
PDFs (brand red header rows, near-black text, light grey borders) and only use the
built-in Helvetica fonts. All user-entered text is XML-escaped before it reaches a
Paragraph, because job names and notes can contain characters like <, > and &.
"""

from __future__ import annotations

from functools import lru_cache
from io import BytesIO
from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_RIGHT
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.pdfgen.canvas import Canvas
from reportlab.platypus import (
    CondPageBreak,
    Flowable,
    HRFlowable,
    Image,
    KeepTogether,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from app.models import PrintJob
from app.services.print_jobs import (
    STATUS_OK,
    STATUS_OUT,
    STATUS_SHORT,
    STATUS_UNLISTED,
    format_micron,
    pkt_now,
)

# --------------------------------------------------------------------------- company details
# Fill these in to change the letterhead on every Job Planning PDF.

COMPANY_NAME = "RN COLOUR"
COMPANY_TAGLINE = "Printing & Packaging"
COMPANY_CONTACT_LINES: list[str] = []  # e.g. ["Industrial Estate, Multan", "Ph: 061-0000000"]

# --------------------------------------------------------------------------- palette

LOGO_PATH = Path(__file__).resolve().parent.parent / "static" / "images" / "rn-colour-logo.png"

BRAND_RED = colors.HexColor("#B21E22")
BRAND_BLACK = colors.HexColor("#1A1A1A")
TEXT_MUTED = colors.HexColor("#6B7280")
TEXT_SOFT = colors.HexColor("#9CA3AF")
BORDER_GREY = colors.HexColor("#D1D5DB")
GRID_GREY = colors.HexColor("#E5E7EB")
LABEL_BG = colors.HexColor("#F9FAFB")
ZEBRA = colors.HexColor("#F7F7F8")

STATUS_COLOURS = {
    # status: (text colour, light background)
    STATUS_OK: (colors.HexColor("#166534"), colors.HexColor("#DCFCE7")),
    STATUS_SHORT: (colors.HexColor("#9A3412"), colors.HexColor("#FFEDD5")),
    STATUS_OUT: (colors.HexColor("#991B1B"), colors.HexColor("#FEE2E2")),
    STATUS_UNLISTED: (colors.HexColor("#4B5563"), colors.HexColor("#F3F4F6")),
}
JOB_STATUS_COLOURS = {
    PrintJob.STATUS_PENDING: colors.HexColor("#B45309"),
    PrintJob.STATUS_IN_PROGRESS: colors.HexColor("#1D4ED8"),
    PrintJob.STATUS_COMPLETED: colors.HexColor("#166534"),
    PrintJob.STATUS_CANCELLED: colors.HexColor("#6B7280"),
}
READY_COLOURS = (colors.HexColor("#166534"), colors.HexColor("#F0FDF4"), colors.HexColor("#16A34A"))
SHORT_COLOURS = (colors.HexColor("#991B1B"), colors.HexColor("#FEF2F2"), colors.HexColor("#DC2626"))

DASH = "—"
PAGE_MARGIN = 0.55 * inch
FRAME_PAD = 6  # SimpleDocTemplate's default frame padding
FOOTER_Y = 0.38 * inch


# --------------------------------------------------------------------------- text helpers


def _t(value) -> str:
    """Escape user text for a Paragraph.

    The micro sign (U+00B5) has no glyph in the built-in fonts and prints blank, so it
    is swapped for the Greek mu (U+03BC), which reportlab draws from the Symbol font.
    """
    text = str(value if value is not None else "").replace("µ", "μ")
    return escape(text)


def _multiline(value) -> str:
    text = str(value or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    return "<br/>".join(_t(line) for line in text.split("\n"))


def _hex(colour) -> str:
    return "#" + colour.hexval()[2:]


def _kg(value, empty: str = DASH) -> str:
    if value is None:
        return empty
    return f"{float(value):,.2f}"


def _count(value) -> str:
    amount = float(value or 0)
    if abs(amount - round(amount)) < 0.005:
        return f"{int(round(amount)):,}"
    return f"{amount:,.2f}"


def _fmt_date(value) -> str:
    return value.strftime("%d-%m-%Y") if value else DASH


def _fmt_stamp(value) -> str:
    return value.strftime("%d-%m-%Y %I:%M %p PKT")


MISSING_STATUSES = (STATUS_OUT, STATUS_UNLISTED)


def _is_missing(row: dict) -> bool:
    return row["status"] in MISSING_STATUSES


def _status_colours(status: str) -> tuple:
    """(text colour, light background); 'unlisted' is shown exactly like 'out'."""
    if status in MISSING_STATUSES:
        return STATUS_COLOURS[STATUS_OUT]
    return STATUS_COLOURS.get(status, STATUS_COLOURS[STATUS_UNLISTED])


def _status_text(row: dict) -> str:
    """Status wording on the Job Card's materials table."""
    status = row["status"]
    if status == STATUS_SHORT:
        return f"SHORT {_kg(row['shortfall_kg'])} kg"
    if status in MISSING_STATUSES:
        return "NOT IN STOCK"
    if status == STATUS_OK:
        return "In Stock"
    return row["status_label"]


def _status_word(row: dict) -> str:
    """Compact status used in the pending-jobs report."""
    status = row["status"]
    if status in MISSING_STATUSES:
        return "NOT IN STOCK"
    return {STATUS_OK: "OK", STATUS_SHORT: "SHORT"}.get(status, row["status_label"].upper())


def _problem_counts(check: dict) -> tuple[list[dict], list[dict]]:
    """(materials not in stock, materials short) for one evaluated job."""
    materials = check.get("materials", [])
    missing = check.get("not_in_stock")
    short = check.get("short")
    if missing is None:
        missing = [row for row in materials if _is_missing(row)]
    if short is None:
        short = [row for row in materials if row["status"] == STATUS_SHORT]
    return missing, short


def _problem_phrase(missing: int, short: int) -> str:
    """e.g. '2 not in stock, 1 short'."""
    parts = []
    if missing:
        parts.append(f"{missing} not in stock")
    if short:
        parts.append(f"{short} short")
    return ", ".join(parts)


# --------------------------------------------------------------------------- styles


def _style(name, **kw) -> ParagraphStyle:
    base = {
        "fontName": "Helvetica",
        "fontSize": 8.5,
        "leading": 10.5,
        "textColor": BRAND_BLACK,
    }
    base.update(kw)
    return ParagraphStyle(name, **base)


S = {
    "company": _style("company", fontName="Helvetica-Bold", fontSize=19, leading=22),
    "tagline": _style("tagline", fontSize=9, leading=11, textColor=BRAND_RED),
    "contact": _style("contact", fontSize=7.5, leading=9.5, textColor=TEXT_MUTED),
    "doc_title": _style(
        "doc_title", fontName="Helvetica-Bold", fontSize=20, leading=23, alignment=TA_RIGHT,
        textColor=BRAND_RED,
    ),
    "doc_meta": _style("doc_meta", fontSize=9, leading=12, alignment=TA_RIGHT),
    "label": _style(
        "label", fontName="Helvetica-Bold", fontSize=7, leading=9, textColor=TEXT_MUTED
    ),
    "value": _style("value", fontSize=9.5, leading=12),
    "value_bold": _style("value_bold", fontName="Helvetica-Bold", fontSize=9.5, leading=12),
    "job_name": _style("job_name", fontName="Helvetica-Bold", fontSize=14, leading=17),
    "section": _style("section", fontName="Helvetica-Bold", fontSize=11, leading=13),
    "section_note": _style(
        "section_note", fontSize=7.5, leading=13, textColor=TEXT_MUTED, alignment=TA_RIGHT
    ),
    "head": _style(
        "head", fontName="Helvetica-Bold", fontSize=7.5, leading=9, textColor=colors.white
    ),
    "head_r": _style(
        "head_r", fontName="Helvetica-Bold", fontSize=7.5, leading=9, textColor=colors.white,
        alignment=TA_RIGHT,
    ),
    "head_c": _style(
        "head_c", fontName="Helvetica-Bold", fontSize=7.5, leading=9, textColor=colors.white,
        alignment=TA_CENTER,
    ),
    "cell": _style("cell", fontSize=8.5, leading=10.5),
    "cell_muted": _style("cell_muted", fontSize=8.5, leading=10.5, textColor=TEXT_MUTED),
    "cell_c": _style("cell_c", fontSize=8.5, leading=10.5, alignment=TA_CENTER),
    "num": _style("num", fontSize=8.5, leading=10.5, alignment=TA_RIGHT),
    "num_bold": _style("num_bold", fontName="Helvetica-Bold", fontSize=8.5, leading=10.5,
                       alignment=TA_RIGHT),
    "status": _style("status", fontName="Helvetica-Bold", fontSize=7.5, leading=9.5,
                     alignment=TA_CENTER),
    "banner_head": _style("banner_head", fontSize=11, leading=14),
    "alert_title": _style("alert_title", fontName="Helvetica-Bold", fontSize=10.5, leading=13,
                          textColor=SHORT_COLOURS[0]),
    "alert_line": _style("alert_line", fontSize=8.5, leading=11.5, leftIndent=9, firstLineIndent=-9),
    "produce": _style("produce", fontName="Helvetica-Bold", fontSize=13, leading=16),
    "banner_sub": _style("banner_sub", fontSize=8, leading=10.5, textColor=colors.HexColor("#374151")),
    "notes": _style("notes", fontSize=9, leading=12.5),
    "sign": _style("sign", fontName="Helvetica-Bold", fontSize=8, leading=10, alignment=TA_CENTER),
    "sign_sub": _style("sign_sub", fontSize=7, leading=9, alignment=TA_CENTER, textColor=TEXT_SOFT),
    "empty": _style("empty", fontSize=10, leading=14, textColor=TEXT_MUTED, alignment=TA_CENTER),
    "tile_num": _style("tile_num", fontName="Helvetica-Bold", fontSize=18, leading=21),
    "tile_text": _style("tile_text", fontName="Helvetica-Bold", fontSize=10, leading=21),
    "tile_label": _style("tile_label", fontName="Helvetica-Bold", fontSize=7, leading=9,
                         textColor=TEXT_MUTED),
    "small": _style("small", fontSize=7.5, leading=9.5),
    "small_r": _style("small_r", fontName="Helvetica-Bold", fontSize=6.8, leading=9.5,
                      alignment=TA_RIGHT),
    "small_rr": _style("small_rr", fontSize=7.5, leading=9.5, alignment=TA_RIGHT),
    "small_c": _style("small_c", fontName="Helvetica-Bold", fontSize=7.5, leading=9.5,
                      alignment=TA_CENTER),
}


# --------------------------------------------------------------------------- logo


@lru_cache(maxsize=1)
def _logo_png() -> tuple[bytes, float] | None:
    """Logo trimmed to its artwork with a pure white background, as (image bytes, aspect).

    The source PNG has a wide off-white margin and an almost-white (251) background,
    which would show as a faint grey box on paper; both are cleaned up here.
    """
    if not LOGO_PATH.exists():
        return None
    try:
        from PIL import Image as PILImage

        with PILImage.open(LOGO_PATH) as source:
            img = source.convert("RGB")
        img = img.point(lambda v: 255 if v >= 244 else v)
        mask = img.convert("L").point(lambda v: 255 if v < 236 else 0)
        box = mask.getbbox()
        if box:
            pad_x = int((box[2] - box[0]) * 0.03)
            pad_y = int((box[3] - box[1]) * 0.03)
            box = (
                max(0, box[0] - pad_x),
                max(0, box[1] - pad_y),
                min(img.width, box[2] + pad_x),
                min(img.height, box[3] + pad_y),
            )
            img = img.crop(box)
        if img.width > 900:
            img = img.resize((900, round(img.height * 900 / img.width)), PILImage.LANCZOS)
        out = BytesIO()
        img.save(out, format="JPEG", quality=92, optimize=True)  # keeps PDFs small
        return out.getvalue(), img.width / img.height
    except Exception:  # noqa: BLE001 - fall back to the raw file
        try:
            from reportlab.lib.utils import ImageReader

            width, height = ImageReader(str(LOGO_PATH)).getSize()
            return LOGO_PATH.read_bytes(), width / height
        except Exception:  # noqa: BLE001
            return None


def _logo(height: float):
    logo = _logo_png()
    if not logo:
        return None
    data, aspect = logo
    return Image(BytesIO(data), width=height * aspect, height=height)


# --------------------------------------------------------------------------- shared blocks


def _letterhead(title: str, meta_lines: list[str], width: float) -> list:
    """Logo + company block on the left, document title and meta on the right."""
    logo_height = 0.72 * inch
    logo = _logo(logo_height)

    company = [Paragraph(_t(COMPANY_NAME), S["company"]), Paragraph(_t(COMPANY_TAGLINE), S["tagline"])]
    for line in COMPANY_CONTACT_LINES:
        if str(line or "").strip():
            company.append(Paragraph(_t(line), S["contact"]))

    right = [Paragraph(_t(title), S["doc_title"]), Spacer(1, 3)]
    right += [Paragraph(line, S["doc_meta"]) for line in meta_lines]

    right_w = 2.6 * inch
    if logo is not None:
        logo_w = logo.drawWidth + 12
        cells = [[logo, company, right]]
        widths = [logo_w, width - logo_w - right_w, right_w]
    else:
        cells = [[company, right]]
        widths = [width - right_w, right_w]

    table = Table(cells, colWidths=widths, hAlign="LEFT")
    table.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
            ]
        )
    )
    return [
        table,
        Spacer(1, 8),
        HRFlowable(width=width, thickness=1.6, color=BRAND_RED, spaceBefore=0, spaceAfter=0),
        Spacer(1, 12),
    ]


def _section_heading(title: str, note: str, width: float, note_share: float = 0.4) -> list:
    """Heading row; starts a new page unless the heading plus a few table rows still fit.

    (keepWithNext would drag a long table and its heading to the next page as a whole.)
    """
    table = Table(
        [[Paragraph(_t(title), S["section"]), Paragraph(_t(note), S["section_note"])]],
        colWidths=[width * (1 - note_share), width * note_share],
        hAlign="LEFT",
    )
    table.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "BOTTOM"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )
    return [CondPageBreak(1.3 * inch), table]


def _data_table(data, widths, *, index_col=False, status_col=None, statuses=(), extra=()) -> Table:
    """Brand-red header row, zebra body, light grid; optional coloured status cells."""
    table = Table(data, colWidths=widths, repeatRows=1, hAlign="LEFT")
    style = [
        ("BACKGROUND", (0, 0), (-1, 0), BRAND_RED),
        ("VALIGN", (0, 0), (-1, 0), "BOTTOM"),
        ("VALIGN", (0, 1), (-1, -1), "MIDDLE"),
        ("BOX", (0, 0), (-1, -1), 0.6, BORDER_GREY),
        ("LINEBELOW", (0, 1), (-1, -1), 0.4, GRID_GREY),
        ("LINEAFTER", (0, 1), (-2, -1), 0.4, GRID_GREY),
        ("TOPPADDING", (0, 0), (-1, 0), 5),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 5),
        ("TOPPADDING", (0, 1), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 1), (-1, -1), 5),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
    ]
    if index_col:
        style += [("LEFTPADDING", (0, 0), (0, -1), 2), ("RIGHTPADDING", (0, 0), (0, -1), 2)]
    if len(data) > 1:
        style.append(("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, ZEBRA]))
    if status_col is not None:
        for index, status in enumerate(statuses, start=1):
            _fg, bg = _status_colours(status)
            style.append(("BACKGROUND", (status_col, index), (status_col, index), bg))
    style.extend(extra)
    table.setStyle(TableStyle(style))
    return table


def _status_cell(row: dict) -> Paragraph:
    fg, _bg = _status_colours(row["status"])
    return Paragraph(f'<font color="{_hex(fg)}">{_t(_status_text(row))}</font>', S["status"])


class _PushToBottom(Flowable):
    """Spacer that takes up whatever room is left above a block of known height.

    Used so the signature row sits at the foot of the last page instead of floating
    directly under the tables. If the block does not fit, it collapses to nothing and
    the block moves to the next page as usual.
    """

    def __init__(self, block_height: float):
        super().__init__()
        self.block_height = block_height
        self.height = 0

    def wrap(self, avail_width, avail_height):
        self.height = max(0.0, avail_height - self.block_height - 1)
        return avail_width, self.height

    def draw(self):
        pass


def _footer_canvas(left_text: str, running_header: str):
    """Canvas class that draws the footer (with 'Page N of M') and a running header
    on continuation pages."""

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
            self.setStrokeColor(GRID_GREY)
            self.setLineWidth(0.5)
            self.line(left, FOOTER_Y + 11, right, FOOTER_Y + 11)
            self.setFont("Helvetica", 7.5)
            self.setFillColor(TEXT_MUTED)
            self.drawString(left, FOOTER_Y, left_text)
            self.drawRightString(right, FOOTER_Y, f"Page {self._pageNumber} of {total}")
            if self._pageNumber > 1:
                self.setFont("Helvetica-Bold", 8)
                self.setFillColor(BRAND_RED)
                self.drawString(left, page_h - 0.38 * inch, COMPANY_NAME)
                self.setFont("Helvetica", 8)
                self.setFillColor(TEXT_MUTED)
                self.drawRightString(right, page_h - 0.38 * inch, running_header)
                self.setStrokeColor(GRID_GREY)
                self.line(left, page_h - 0.38 * inch - 5, right, page_h - 0.38 * inch - 5)
            self.restoreState()

    return FooterCanvas


def _doc(buffer: BytesIO, pagesize, title: str) -> SimpleDocTemplate:
    return SimpleDocTemplate(
        buffer,
        pagesize=pagesize,
        leftMargin=PAGE_MARGIN,
        rightMargin=PAGE_MARGIN,
        topMargin=0.6 * inch,
        bottomMargin=0.7 * inch,
        title=title,
        author=COMPANY_NAME,
        creator=f"{COMPANY_NAME} Job Planning",
    )


# --------------------------------------------------------------------------- job card


def _job_details(job: PrintJob, printed_on: str, width: float) -> Table:
    label_w = 1.12 * inch
    value_w = (width - 2 * label_w) / 2
    status_colour = JOB_STATUS_COLOURS.get(job.status, BRAND_BLACK)
    prepared_by = job.created_by.username if job.created_by else DASH

    def label(text):
        return Paragraph(_t(text.upper()), S["label"])

    def value(text, style="value"):
        return Paragraph(text, S[style])

    production = (job.production_label or "").strip()
    produce = _t(production) if production else f'<font color="{_hex(TEXT_SOFT)}">{DASH}</font>'

    data = [
        [label("Job Name"), value(_t(job.job_name), "job_name"), "", ""],
        [label("Total to Produce"), value(produce, "produce"), "", ""],
        [label("Job No"), value(_t(job.job_number), "value_bold"),
         label("Job Date"), value(_fmt_date(job.job_date))],
        [label("Customer / Party"), value(_t(job.customer_name or DASH)),
         label("Status"),
         value(f'<font color="{_hex(status_colour)}">{_t(job.status_label)}</font>', "value_bold")],
        [label("Prepared By"), value(_t(prepared_by)),
         label("Printed On"), value(_t(printed_on))],
    ]
    table = Table(data, colWidths=[label_w, value_w, label_w, value_w], hAlign="LEFT")
    table.setStyle(
        TableStyle(
            [
                ("SPAN", (1, 0), (3, 0)),
                ("SPAN", (1, 1), (3, 1)),
                ("BOX", (0, 0), (-1, -1), 0.6, BORDER_GREY),
                ("INNERGRID", (0, 0), (-1, -1), 0.4, GRID_GREY),
                ("BACKGROUND", (0, 0), (0, -1), LABEL_BG),
                ("BACKGROUND", (2, 2), (2, -1), LABEL_BG),
                ("TOPPADDING", (0, 1), (-1, 1), 7),
                ("BOTTOMPADDING", (0, 1), (-1, 1), 7),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 7),
                ("RIGHTPADDING", (0, 0), (-1, -1), 7),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (-1, 0), 8),
                ("BOTTOMPADDING", (0, 0), (-1, 0), 8),
            ]
        )
    )
    return table


def _verdict_banner(check: dict, printed_on: str, width: float) -> Table:
    job = check["job"]
    ready = check["ready"]
    count = check["problem_count"]
    missing, short = _problem_counts(check)
    fg, bg, bar = READY_COLOURS if ready else SHORT_COLOURS
    colour = _hex(fg)
    phrase = _problem_phrase(len(missing), len(short))

    if job.is_open:
        if ready:
            head = f'<font color="{colour}"><b>READY TO RUN</b></font> — all materials are in stock'
        else:
            head = (
                f'<font color="{colour}"><b>STOCK SHORT</b></font> — '
                f"{count} material(s) need attention"
            )
            if phrase:
                head += f": <b>{_t(phrase)}</b>"
        sub = f"Checked against In Hall and Machine Hall stock on {_t(printed_on)}."
    else:
        if ready:
            result = "all materials are in stock"
        else:
            result = f"<b>{_t(phrase or f'{count} material(s) need attention')}</b>"
        head = f'<font color="{colour}"><b>STOCK CHECK AT TIME OF PRINTING</b></font> — {result}'
        sub = (
            f"This job is {_t(job.status_label)}; figures show In Hall and Machine Hall stock on "
            f"{_t(printed_on)}, not stock when the job ran."
        )
    if not ready:
        sub += " Details are listed below."

    bar_w = 4
    table = Table(
        [["", Paragraph(head, S["banner_head"])], ["", Paragraph(sub, S["banner_sub"])]],
        colWidths=[bar_w, width - bar_w],
        hAlign="LEFT",
    )
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), bg),
                ("BACKGROUND", (0, 0), (0, -1), bar),
                ("SPAN", (0, 0), (0, -1)),
                ("LEFTPADDING", (1, 0), (1, -1), 10),
                ("RIGHTPADDING", (0, 0), (-1, -1), 10),
                ("TOPPADDING", (0, 0), (-1, 0), 8),
                ("BOTTOMPADDING", (0, 0), (-1, 0), 1),
                ("TOPPADDING", (0, 1), (-1, 1), 1),
                ("BOTTOMPADDING", (0, 1), (-1, 1), 8),
            ]
        )
    )
    return table


def _stock_alert_box(check: dict, width: float) -> Table | None:
    """Red-bordered box naming every material that is not in stock or short."""
    missing, short = _problem_counts(check)
    if not missing and not short:
        return None
    red = _hex(SHORT_COLOURS[0])
    orange = _hex(STATUS_COLOURS[STATUS_SHORT][0])

    if missing:
        title = f"MATERIALS NOT IN STOCK ({len(missing)})"
        if short:
            title += f" · SHORT ({len(short)})"
    else:
        title = f"MATERIALS SHORT ({len(short)})"

    rows = [[Paragraph(_t(title), S["alert_title"])]]
    for row in missing:
        parts = [f'<font color="{red}"><b>NOT IN STOCK:</b></font> <b>{_t(row["label"])}</b>']
        if row["required_kg"] is not None:
            parts.append(f"required {_kg(row['required_kg'])} kg")
        parts.append("0 kg in In Hall / Machine Hall")
        rows.append([Paragraph("• " + " — ".join(parts), S["alert_line"])])
    for row in short:
        need = _kg(row["required_kg"]) if row["required_kg"] is not None else DASH
        rows.append(
            [
                Paragraph(
                    f'• <font color="{orange}"><b>SHORT:</b></font> <b>{_t(row["label"])}</b> — '
                    f"need {need} kg, available {_kg(row['total_kg'])} kg "
                    f'<font color="{orange}">(short {_kg(row["shortfall_kg"])} kg)</font>',
                    S["alert_line"],
                )
            ]
        )

    table = Table(rows, colWidths=[width], hAlign="LEFT")
    table.setStyle(
        TableStyle(
            [
                ("BOX", (0, 0), (-1, -1), 1.4, SHORT_COLOURS[2]),
                ("BACKGROUND", (0, 0), (-1, 0), STATUS_COLOURS[STATUS_OUT][1]),
                ("BACKGROUND", (0, 1), (-1, -1), colors.white),
                ("LINEBELOW", (0, 0), (-1, 0), 0.8, SHORT_COLOURS[2]),
                ("LEFTPADDING", (0, 0), (-1, -1), 10),
                ("RIGHTPADDING", (0, 0), (-1, -1), 10),
                ("TOPPADDING", (0, 0), (-1, 0), 6),
                ("BOTTOMPADDING", (0, 0), (-1, 0), 6),
                ("TOPPADDING", (0, 1), (-1, -1), 2.5),
                ("BOTTOMPADDING", (0, 1), (-1, -1), 2.5),
                ("TOPPADDING", (0, 1), (-1, 1), 6),
                ("BOTTOMPADDING", (0, -1), (-1, -1), 7),
            ]
        )
    )
    return table


def _stock_cell(kg: float, rolls: float = 0.0, *, listed: bool = True, bold: bool = False) -> Paragraph:
    if not listed:
        return Paragraph(f'<font color="{_hex(TEXT_SOFT)}">{DASH}</font>', S["num"])
    style = S["num_bold"] if bold else S["num"]
    if kg <= 0.0005 and not bold:
        text = f'<font color="{_hex(TEXT_SOFT)}">0.00</font>'
    else:
        text = _kg(kg)
    if rolls > 0.0005:
        unit = "roll" if abs(rolls - 1) < 0.005 else "rolls"
        text += f'<br/><font size="6.5" color="{_hex(TEXT_MUTED)}">{_count(rolls)} {unit}</font>'
    return Paragraph(text, style)


def _material_name_cell(row: dict) -> Paragraph:
    """Material name in bold with category / size / micron underneath."""
    details = [row["category"], row["size"], format_micron(row["micron"])]
    seen = []
    for part in details:
        part = (part or "").strip()
        if part and part.casefold() != (row["name"] or "").casefold() and part not in seen:
            seen.append(part)
    text = f"<b>{_t(row['name'])}</b>"
    if seen:
        text += f'<br/><font size="7.5" color="{_hex(TEXT_MUTED)}">{_t(" · ".join(seen))}</font>'
    return Paragraph(text, S["cell"])


def _materials_table(rows: list[dict], width: float) -> Table:
    fixed = [0.3, 0.9, 0.85, 0.85, 0.95, 1.05]  # inches, all but the Material column
    widths = [fixed[0] * inch, 0, *(w * inch for w in fixed[1:])]
    widths[1] = width - sum(widths)
    head = [
        Paragraph("#", S["head_c"]),
        Paragraph("Material", S["head"]),
        Paragraph("Required KG", S["head_r"]),
        Paragraph("In Hall", S["head_r"]),
        Paragraph("Machine Hall", S["head_r"]),
        Paragraph("Total Available", S["head_r"]),
        Paragraph("Status", S["head_c"]),
    ]
    data = [head]
    for row in rows:
        listed = row["status"] != STATUS_UNLISTED
        data.append(
            [
                Paragraph(str(row["position"]), S["cell_c"]),
                _material_name_cell(row),
                Paragraph(_kg(row["required_kg"]), S["num_bold"]),
                _stock_cell(row["in_hall_kg"], row["in_hall_rolls"], listed=listed),
                _stock_cell(row["machine_kg"], row["machine_rolls"], listed=listed),
                _stock_cell(row["total_kg"], listed=listed, bold=True),
                _status_cell(row),
            ]
        )
    if not rows:
        data.append(["", Paragraph("No materials on this job.", S["cell_muted"])] + [""] * 5)
    return _data_table(
        data, widths, index_col=True, status_col=6, statuses=[r["status"] for r in rows],
        extra=[("SPAN", (1, 1), (-1, 1))] if not rows else [],
    )


def _notes_box(notes: str, width: float) -> Table:
    table = Table(
        [[Paragraph("NOTES / INSTRUCTIONS", S["label"])], [Paragraph(_multiline(notes), S["notes"])]],
        colWidths=[width],
        hAlign="LEFT",
    )
    table.setStyle(
        TableStyle(
            [
                ("BOX", (0, 0), (-1, -1), 0.6, BORDER_GREY),
                ("BACKGROUND", (0, 0), (-1, -1), LABEL_BG),
                ("LEFTPADDING", (0, 0), (-1, -1), 10),
                ("RIGHTPADDING", (0, 0), (-1, -1), 10),
                ("TOPPADDING", (0, 0), (-1, 0), 7),
                ("BOTTOMPADDING", (0, 0), (-1, 0), 2),
                ("TOPPADDING", (0, 1), (-1, 1), 2),
                ("BOTTOMPADDING", (0, 1), (-1, 1), 9),
            ]
        )
    )
    return table


def _signature_row(width: float) -> Table:
    roles = ["Prepared By", "Checked By", "Production Manager", "Approved By"]
    gap = 0.3 * inch
    col = (width - gap * (len(roles) - 1)) / len(roles)
    widths = []
    for index in range(len(roles)):
        widths.append(col)
        if index < len(roles) - 1:
            widths.append(gap)
    space_row, line_row, name_row, date_row = [], [], [], []
    for index, role in enumerate(roles):
        space_row.append("")
        line_row.append("")
        name_row.append(Paragraph(_t(role), S["sign"]))
        date_row.append(Paragraph("Name &amp; Date", S["sign_sub"]))
        if index < len(roles) - 1:
            for row in (space_row, line_row, name_row, date_row):
                row.append("")
    table = Table([space_row, name_row, date_row], colWidths=widths, rowHeights=[34, None, None], hAlign="LEFT")
    style = [
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 1),
        ("TOPPADDING", (0, 1), (-1, 1), 4),
    ]
    for index in range(len(roles)):
        style.append(("LINEBELOW", (index * 2, 0), (index * 2, 0), 0.8, BRAND_BLACK))
    table.setStyle(TableStyle(style))
    return table


def generate_job_card_pdf(check: dict) -> BytesIO:
    """A4 portrait Job Card for one job, with a live stock check."""
    job: PrintJob = check["job"]
    printed_on = _fmt_stamp(pkt_now())

    buffer = BytesIO()
    doc = _doc(buffer, A4, f"Job Card {job.job_number}")
    width = doc.width

    story = _letterhead(
        "JOB CARD",
        [
            f'Job No: <b>{_t(job.job_number)}</b>',
            f"Date: <b>{_fmt_date(job.job_date)}</b>",
        ],
        width,
    )
    story += [
        _job_details(job, printed_on, width),
        Spacer(1, 12),
        _verdict_banner(check, printed_on, width),
    ]
    alert = _stock_alert_box(check, width)
    if alert is not None:
        story += [Spacer(1, 8), KeepTogether([alert])]
    story += [
        Spacer(1, 16),
        *_section_heading(
            f"Materials Required ({len(check['materials'])})",
            "All quantities in KG · Available = In Hall + Machine Hall",
            width,
        ),
        _materials_table(check["materials"], width),
    ]
    if (job.notes or "").strip():
        story += [Spacer(1, 14), KeepTogether([_notes_box(job.notes, width)])]

    signatures = _signature_row(width)
    _w, sig_height = signatures.wrap(width, doc.height)
    story += [Spacer(1, 24), _PushToBottom(sig_height), KeepTogether([signatures])]

    doc.build(
        story,
        canvasmaker=_footer_canvas(
            f"{COMPANY_NAME} · Job Card {job.job_number}",
            f"Job Card {job.job_number} (continued)",
        ),
    )
    buffer.seek(0)
    return buffer


# --------------------------------------------------------------------------- pending report


def _summary_strip(rows: list[dict], generated: str, width: float) -> Table:
    ready = sum(1 for row in rows if row["ready"])
    short = len(rows) - ready

    def tile(number, label, colour=BRAND_BLACK, *, text=False):
        style = S["tile_text"] if text else S["tile_num"]
        return [
            Paragraph(f'<font color="{_hex(colour)}">{number}</font>', style),
            Paragraph(_t(label.upper()), S["tile_label"]),
        ]

    cells = [
        tile(len(rows), "Total open jobs"),
        tile(ready, "Ready to run", READY_COLOURS[0]),
        tile(short, "Stock short", SHORT_COLOURS[0] if short else BRAND_BLACK),
        tile(_t(generated), "Generated", text=True),
    ]
    widths = [width * 0.2, width * 0.2, width * 0.2, width * 0.4]
    table = Table([cells], colWidths=widths, hAlign="LEFT")
    table.setStyle(
        TableStyle(
            [
                ("BOX", (0, 0), (-1, -1), 0.6, BORDER_GREY),
                ("LINEAFTER", (0, 0), (-2, -1), 0.4, GRID_GREY),
                ("BACKGROUND", (0, 0), (-1, -1), LABEL_BG),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 12),
                ("TOPPADDING", (0, 0), (-1, -1), 8),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
            ]
        )
    )
    return table


def _line_list(lines: list[tuple[str, dict]], width: float) -> Table | Paragraph:
    """Nested two-column list: description on the left, coloured status word on the right."""
    if not lines:
        return Paragraph(f'<font color="{_hex(TEXT_SOFT)}">{DASH}</font>', S["small"])
    word_w = 0.8 * inch
    data = []
    for text, row in lines:
        fg, _bg = _status_colours(row["status"])
        data.append(
            [
                Paragraph(text, S["small"]),
                Paragraph(f'<font color="{_hex(fg)}">{_t(_status_word(row))}</font>', S["small_r"]),
            ]
        )
    table = Table(data, colWidths=[width - word_w, word_w], hAlign="LEFT")
    table.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (0, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 1.5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 1.5),
                ("LINEBELOW", (0, 0), (-1, -2), 0.3, GRID_GREY),
            ]
        )
    )
    return table


def _material_line(row: dict) -> str:
    muted = _hex(TEXT_MUTED)
    if _is_missing(row):
        stock = "0 kg in In Hall / Machine Hall"
    else:
        stock = f"{_kg(row['total_kg'])} kg"
    if row["required_kg"] is not None:
        stock += f" (need {_kg(row['required_kg'])})"
    return f'{_t(row["label"])}<br/><font color="{muted}">{_t(stock)}</font>'


def _stock_summary(row: dict) -> tuple[str, tuple]:
    """Per-job Stock cell for the pending report: text and (fg, bg)."""
    if row["ready"]:
        fg, bg, _bar = READY_COLOURS
        return "READY", (fg, bg)
    missing, short = _problem_counts(row)
    parts = []
    if missing:
        parts.append(f"{len(missing)} NOT IN STOCK")
    if short:
        parts.append(f"{len(short)} SHORT")
    if not parts:
        parts.append(f"{row['problem_count']} SHORT")
    colours = SHORT_COLOURS[:2] if missing else STATUS_COLOURS[STATUS_SHORT]
    return " /<br/>".join(parts), colours


def generate_pending_jobs_pdf(rows: list[dict]) -> BytesIO:
    """A4 landscape report of all open (pending / in printing) jobs with their stock state."""
    now = pkt_now()
    generated = _fmt_stamp(now)

    buffer = BytesIO()
    doc = _doc(buffer, landscape(A4), "Pending Jobs")
    width = doc.width

    story = _letterhead(
        "PENDING JOBS",
        [f"As of <b>{now.strftime('%d-%m-%Y')}</b>", "Pending and In Printing, oldest first"],
        width,
    )
    story += [_summary_strip(rows, generated, width), Spacer(1, 16)]

    if not rows:
        story.append(Spacer(1, 30))
        story.append(Paragraph("No pending jobs.", S["empty"]))
        story.append(
            Paragraph(
                "Every job is completed or cancelled. New jobs will appear here.",
                ParagraphStyle("empty_sub", parent=S["contact"], alignment=TA_CENTER),
            )
        )
    else:
        col = {
            "no": 0.74, "date": 0.74, "name": 1.5, "customer": 1.15, "produce": 0.95,
            "status": 0.76, "stock": 1.12,
        }
        widths = [
            col["no"] * inch, col["date"] * inch, col["name"] * inch, col["customer"] * inch,
            col["produce"] * inch, 0, col["status"] * inch, col["stock"] * inch,
        ]
        widths[5] = width - sum(widths)
        pad = 10  # left + right cell padding
        head = [
            Paragraph("Job No", S["head"]),
            Paragraph("Date", S["head"]),
            Paragraph("Job Name", S["head"]),
            Paragraph("Customer", S["head"]),
            Paragraph("To Produce", S["head_r"]),
            Paragraph("Materials", S["head"]),
            Paragraph("Status", S["head_c"]),
            Paragraph("Stock", S["head_c"]),
        ]
        data = [head]
        extra = []
        for index, row in enumerate(rows, start=1):
            job = row["job"]
            status_colour = JOB_STATUS_COLOURS.get(job.status, BRAND_BLACK)
            stock_text, (fg, bg) = _stock_summary(row)
            production = (job.production_label or "").strip()
            produce = (
                f"<b>{_t(production)}</b>" if production
                else f'<font color="{_hex(TEXT_SOFT)}">{DASH}</font>'
            )
            extra.append(("BACKGROUND", (7, index), (7, index), bg))
            data.append(
                [
                    Paragraph(f"<b>{_t(job.job_number)}</b>", S["small"]),
                    Paragraph(_fmt_date(job.job_date), S["small"]),
                    Paragraph(f"<b>{_t(job.job_name)}</b>", S["small"]),
                    Paragraph(_t(job.customer_name or DASH), S["small"]),
                    Paragraph(produce, S["small_rr"]),
                    _line_list([(_material_line(m), m) for m in row["materials"]], widths[5] - pad),
                    Paragraph(f'<font color="{_hex(status_colour)}">{_t(job.status_label)}</font>',
                              S["small_c"]),
                    Paragraph(f'<font color="{_hex(fg)}">{stock_text}</font>', S["small_c"]),
                ]
            )
        extra += [("VALIGN", (0, 1), (-1, -1), "TOP"), ("VALIGN", (6, 1), (7, -1), "MIDDLE")]
        story += _section_heading(
            f"Open Jobs ({len(rows)})",
            "Stock per material: OK = enough · SHORT = not enough · "
            "NOT IN STOCK = 0 kg in In Hall / Machine Hall",
            width,
            note_share=0.65,
        )
        story.append(_data_table(data, widths, extra=extra))

    doc.build(
        story,
        canvasmaker=_footer_canvas(
            f"{COMPANY_NAME} · Pending Jobs · {generated}",
            "Pending Jobs (continued)",
        ),
    )
    buffer.seek(0)
    return buffer
