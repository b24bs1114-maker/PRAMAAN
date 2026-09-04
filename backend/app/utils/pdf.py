"""Minimal, dependency-free PDF writer.

PRAMAAN's forensic report is normally rendered with ReportLab. This module is the
fallback used when ReportLab is not installed: it emits a real, standards-valid
PDF 1.4 file using only the base-14 fonts (which every reader has built in, so no
font data needs embedding).

Scope is deliberately narrow -- left-aligned text, rules, simple tables and page
breaks -- because that is all the report needs. It is not a general typesetting
engine: there is no kerning, no justification, no image support, and bold metrics
are approximated from the regular Helvetica widths.

Both this writer and the ReportLab renderer consume the same block list, so the
report's *content* is identical either way; only its typography differs.
"""

from __future__ import annotations

from typing import Any

WRITER = "pramaan-minipdf/1.0"

PAGE_WIDTH = 612.0   # US Letter, 72 dpi user units
PAGE_HEIGHT = 792.0
MARGIN = 54.0
CONTENT_WIDTH = PAGE_WIDTH - 2 * MARGIN
FOOTER_Y = 34.0
BOTTOM_LIMIT = MARGIN + 14.0

FONT_REGULAR = "F1"
FONT_BOLD = "F2"
FONT_MONO = "F3"

_FONTS = {
    FONT_REGULAR: "Helvetica",
    FONT_BOLD: "Helvetica-Bold",
    FONT_MONO: "Courier",
}

# Adobe AFM advance widths for Helvetica, characters 32..126, in 1/1000 em.
_HELVETICA_WIDTHS = (
    278, 278, 355, 556, 556, 889, 667, 191, 333, 333, 389, 584, 278, 333, 278,
    278, 556, 556, 556, 556, 556, 556, 556, 556, 556, 556, 278, 278, 584, 584,
    584, 556, 1015, 667, 667, 722, 722, 667, 611, 778, 722, 278, 500, 667, 556,
    833, 722, 778, 667, 778, 722, 667, 611, 722, 667, 944, 667, 667, 611, 278,
    278, 278, 469, 556, 333, 556, 556, 500, 556, 556, 278, 556, 556, 222, 222,
    500, 222, 833, 556, 556, 556, 556, 333, 500, 278, 556, 500, 722, 500, 500,
    500, 334, 260, 334, 584,
)
assert len(_HELVETICA_WIDTHS) == 95

_BOLD_FACTOR = 1.08   # Helvetica-Bold runs wider; over-estimate rather than clip
_MONO_WIDTH = 600     # Courier is monospaced


def _char_width(code: int, font: str) -> int:
    if font == FONT_MONO:
        return _MONO_WIDTH
    width = _HELVETICA_WIDTHS[code - 32] if 32 <= code <= 126 else 556
    return int(width * _BOLD_FACTOR) if font == FONT_BOLD else width


def text_width(text: str, font: str, size: float) -> float:
    """Advance width of ``text`` in user units."""
    return sum(_char_width(ord(ch), font) for ch in text) * size / 1000.0


def _sanitise(text: str) -> str:
    """Reduce to WinAnsi-safe characters, mapping common typography to ASCII."""
    replacements = {
        "‘": "'", "’": "'", "“": '"', "”": '"',
        "–": "-", "—": "--", "…": "...", " ": " ",
        "→": "->", "×": "x", "≥": ">=", "≤": "<=",
        # Neither is latin-1, so without these entries a bullet reached the
        # unmappable branch below and printed as "?". (The ReportLab renderer in
        # report.py drops bullets for its own reason: it reverse-maps U+2022 onto
        # WinAnsi 0x7F, which that encoding leaves undefined.)
        "•": "-", "·": "-",
    }
    out = []
    for ch in str(text):
        if ch in replacements:
            out.append(replacements[ch])
        elif ch == "\t":
            out.append("    ")
        elif ord(ch) < 32:
            out.append(" ")
        elif ord(ch) < 127:
            out.append(ch)
        else:
            try:
                ch.encode("latin-1")
            except UnicodeEncodeError:
                out.append("?")
            else:
                out.append(ch)
    return "".join(out)


def _escape(text: str) -> bytes:
    escaped = (
        _sanitise(text)
        .replace("\\", r"\\")
        .replace("(", r"\(")
        .replace(")", r"\)")
    )
    return escaped.encode("latin-1", "replace")


def wrap(text: str, font: str, size: float, max_width: float) -> list[str]:
    """Greedy word wrap; over-long words are split so nothing ever overflows."""
    text = _sanitise(text)
    if not text.strip():
        return [""]

    lines: list[str] = []
    for paragraph in text.split("\n"):
        current = ""
        for word in paragraph.split():
            if text_width(word, font, size) > max_width:
                # A single word wider than the column: break it by character.
                if current:
                    lines.append(current)
                    current = ""
                chunk = ""
                for ch in word:
                    if chunk and text_width(chunk + ch, font, size) > max_width:
                        lines.append(chunk)
                        chunk = ch
                    else:
                        chunk += ch
                current = chunk
                continue

            candidate = f"{current} {word}" if current else word
            if text_width(candidate, font, size) <= max_width:
                current = candidate
            else:
                lines.append(current)
                current = word
        lines.append(current)
    return lines or [""]


class _Page:
    def __init__(self) -> None:
        self.ops: list[bytes] = []
        self.y = PAGE_HEIGHT - MARGIN


class Canvas:
    """Accumulates drawing operations page by page."""

    def __init__(self) -> None:
        self._pages: list[_Page] = [_Page()]

    # -- geometry ---------------------------------------------------------- #
    @property
    def page(self) -> _Page:
        return self._pages[-1]

    @property
    def page_count(self) -> int:
        return len(self._pages)

    def new_page(self) -> None:
        self._pages.append(_Page())

    def ensure(self, needed: float) -> None:
        if self.page.y - needed < BOTTOM_LIMIT:
            self.new_page()

    def advance(self, amount: float) -> None:
        self.page.y -= amount

    # -- primitives -------------------------------------------------------- #
    def draw_text(self, text: str, x: float, y: float, font: str, size: float) -> None:
        self.page.ops.append(
            b"BT /%s %s Tf 1 0 0 1 %s %s Tm (%s) Tj ET"
            % (
                font.encode("ascii"),
                _num(size),
                _num(x),
                _num(y),
                _escape(text),
            )
        )

    def draw_rule(self, y: float, width: float = 0.6, x0: float | None = None,
                  x1: float | None = None, grey: float = 0.35) -> None:
        x0 = MARGIN if x0 is None else x0
        x1 = PAGE_WIDTH - MARGIN if x1 is None else x1
        self.page.ops.append(
            b"q %s G %s w %s %s m %s %s l S Q"
            % (_num(grey), _num(width), _num(x0), _num(y), _num(x1), _num(y))
        )

    def draw_lines(
        self,
        lines: list[str],
        *,
        x: float,
        font: str,
        size: float,
        leading: float,
    ) -> None:
        """Draw pre-wrapped lines, breaking pages as needed."""
        for line in lines:
            self.ensure(leading)
            self.draw_text(line, x, self.page.y - size, font, size)
            self.advance(leading)

    # -- output ------------------------------------------------------------ #
    def footer(self, text: str) -> None:
        """Stamp a footer with page numbers once the total is known."""
        total = len(self._pages)
        for number, page in enumerate(self._pages, start=1):
            page.ops.append(
                b"q 0.45 G 0.5 w %s %s m %s %s l S Q"
                % (_num(MARGIN), _num(FOOTER_Y + 12), _num(PAGE_WIDTH - MARGIN),
                   _num(FOOTER_Y + 12))
            )
            page.ops.append(
                b"BT /%s 7.5 Tf 1 0 0 1 %s %s Tm (%s) Tj ET"
                % (FONT_REGULAR.encode("ascii"), _num(MARGIN), _num(FOOTER_Y),
                   _escape(text))
            )
            label = f"Page {number} of {total}"
            page.ops.append(
                b"BT /%s 7.5 Tf 1 0 0 1 %s %s Tm (%s) Tj ET"
                % (
                    FONT_REGULAR.encode("ascii"),
                    _num(PAGE_WIDTH - MARGIN - text_width(label, FONT_REGULAR, 7.5)),
                    _num(FOOTER_Y),
                    _escape(label),
                )
            )

    def to_bytes(self, *, title: str, author: str, subject: str,
                 created: str | None = None) -> bytes:
        return _serialise(
            [b"\n".join(page.ops) for page in self._pages],
            title=title,
            author=author,
            subject=subject,
            created=created,
        )


def _num(value: float) -> bytes:
    return f"{value:.2f}".rstrip("0").rstrip(".").encode("ascii") or b"0"


def _pdf_date(created: str | None) -> bytes:
    """ISO-8601 UTC (``2026-01-02T03:04:05Z``) to a PDF date string."""
    if not created:
        return b"D:19700101000000Z"
    digits = "".join(ch for ch in created if ch.isdigit())[:14].ljust(14, "0")
    return b"D:" + digits.encode("ascii") + b"Z"


def _serialise(streams: list[bytes], *, title: str, author: str, subject: str,
               created: str | None) -> bytes:
    """Assemble objects, xref table and trailer into a PDF 1.4 file."""
    # Fixed object numbering: 1 catalog, 2 pages, 3-5 fonts, 6 info,
    # then a (page, content) pair per page.
    page_count = len(streams)
    first_page_obj = 7
    page_ids = [first_page_obj + 2 * i for i in range(page_count)]

    objects: dict[int, bytes] = {
        1: b"<< /Type /Catalog /Pages 2 0 R >>",
        2: (
            b"<< /Type /Pages /Count %d /Kids [%s] >>"
            % (
                page_count,
                b" ".join(b"%d 0 R" % pid for pid in page_ids),
            )
        ),
        6: (
            b"<< /Title (%s) /Author (%s) /Subject (%s) /Producer (%s) "
            b"/Creator (%s) /CreationDate (%s) >>"
            % (
                _escape(title),
                _escape(author),
                _escape(subject),
                _escape(WRITER),
                _escape(WRITER),
                _pdf_date(created),
            )
        ),
    }
    for index, (name, base_font) in enumerate(_FONTS.items()):
        objects[3 + index] = (
            b"<< /Type /Font /Subtype /Type1 /Name /%s /BaseFont /%s "
            b"/Encoding /WinAnsiEncoding >>"
            % (name.encode("ascii"), base_font.encode("ascii"))
        )

    resources = b"<< /Font << %s >> >>" % b" ".join(
        b"/%s %d 0 R" % (name.encode("ascii"), 3 + index)
        for index, name in enumerate(_FONTS)
    )

    for index, stream in enumerate(streams):
        page_id = page_ids[index]
        content_id = page_id + 1
        objects[page_id] = (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 %s %s] "
            b"/Resources %s /Contents %d 0 R >>"
            % (_num(PAGE_WIDTH), _num(PAGE_HEIGHT), resources, content_id)
        )
        objects[content_id] = (
            b"<< /Length %d >>\nstream\n%s\nendstream" % (len(stream), stream)
        )

    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets: dict[int, int] = {}
    for number in sorted(objects):
        offsets[number] = len(out)
        out += b"%d 0 obj\n" % number + objects[number] + b"\nendobj\n"

    xref_offset = len(out)
    highest = max(objects)
    out += b"xref\n0 %d\n" % (highest + 1)
    out += b"0000000000 65535 f \n"
    for number in range(1, highest + 1):
        if number in offsets:
            out += b"%010d 00000 n \n" % offsets[number]
        else:  # unused slot; must still occupy a row
            out += b"0000000000 65535 f \n"
    out += (
        b"trailer\n<< /Size %d /Root 1 0 R /Info 6 0 R >>\nstartxref\n%d\n%%%%EOF\n"
        % (highest + 1, xref_offset)
    )
    return bytes(out)


# --------------------------------------------------------------------------- #
# Block rendering
# --------------------------------------------------------------------------- #
KEY_COLUMN = 150.0


def render(blocks: list[dict[str, Any]], *, title: str, author: str, subject: str,
           footer: str, created: str | None = None) -> tuple[bytes, int]:
    """Render the report block list to PDF bytes and a page count."""
    canvas = Canvas()

    for block in blocks:
        kind = block.get("type", "paragraph")

        if kind == "pagebreak":
            canvas.new_page()
        elif kind == "page_header":
            canvas.draw_text("PRAMAAN", MARGIN, canvas.page.y - 14, FONT_BOLD, 14)
            canvas.draw_text("DIGITAL EVIDENCE EXAMINATION", MARGIN, canvas.page.y - 24, FONT_REGULAR, 7)
            
            case_label = f"CASE {block.get('case_number', '')}"
            canvas.draw_text(case_label, PAGE_WIDTH - MARGIN - text_width(case_label, FONT_BOLD, 10), canvas.page.y - 12, FONT_BOLD, 10)
            title_label = str(block.get("title", ""))
            canvas.draw_text(title_label, PAGE_WIDTH - MARGIN - text_width(title_label, FONT_REGULAR, 8.5), canvas.page.y - 23, FONT_REGULAR, 8.5)
            
            canvas.advance(28)
            canvas.draw_rule(canvas.page.y, width=1.0, grey=0.15)
            canvas.advance(8)
        elif kind == "notice":
            text = f"PROTOTYPE OUTPUT  {block.get('text', '')}"
            lines = wrap(text, FONT_REGULAR, 7.5, CONTENT_WIDTH - 12)
            height = len(lines) * 9.5 + 8
            canvas.ensure(height)
            canvas.draw_rule(canvas.page.y, width=0.5, grey=0.7)
            for idx, line in enumerate(lines):
                canvas.draw_text(line, MARGIN + 6, canvas.page.y - 9 - idx * 9.5, FONT_REGULAR, 7.5)
            canvas.advance(height)
            canvas.draw_rule(canvas.page.y, width=0.5, grey=0.7)
            canvas.advance(8)
        elif kind == "summary_bar":
            canvas.ensure(24)
            canvas.draw_rule(canvas.page.y, width=0.5, grey=0.7)
            col_w = CONTENT_WIDTH / 4.0
            for idx, r in enumerate(block.get("rows", [])):
                x = MARGIN + idx * col_w + 4
                canvas.draw_text(r[0], x, canvas.page.y - 8, FONT_REGULAR, 7)
                canvas.draw_text(r[1], x, canvas.page.y - 18, FONT_BOLD, 8.5)
            canvas.advance(22)
            canvas.draw_rule(canvas.page.y, width=0.5, grey=0.7)
            canvas.advance(8)
        elif kind == "verdict_card":
            verdict = str(block.get("verdict", ""))
            score_line = str(block.get("score_line", ""))
            leading = str(block.get("leading", ""))
            
            canvas.ensure(46)
            canvas.draw_rule(canvas.page.y, width=1.5, grey=0.2)
            canvas.draw_text(verdict, MARGIN + 8, canvas.page.y - 16, FONT_BOLD, 16)
            canvas.draw_text(score_line, MARGIN + 8, canvas.page.y - 28, FONT_BOLD, 8.5)
            canvas.draw_text(leading, MARGIN + 8, canvas.page.y - 38, FONT_REGULAR, 7.5)
            canvas.advance(42)
            canvas.draw_rule(canvas.page.y, width=1.5, grey=0.2)
            canvas.advance(8)
        elif kind == "kv_grid":
            for r in block.get("rows", []):
                canvas.ensure(12)
                canvas.draw_text(r[0], MARGIN, canvas.page.y - 9, FONT_BOLD, 8)
                canvas.draw_text(r[1], MARGIN + 90, canvas.page.y - 9, FONT_REGULAR, 8)
                canvas.draw_text(r[2], MARGIN + 260, canvas.page.y - 9, FONT_BOLD, 8)
                canvas.draw_text(r[3], MARGIN + 350, canvas.page.y - 9, FONT_REGULAR, 8)
                canvas.advance(11)
            canvas.advance(4)
        elif kind == "lineage_flow":
            canvas.ensure(32)
            canvas.draw_rule(canvas.page.y, width=0.5, grey=0.7)
            w = (CONTENT_WIDTH - 30) / 3.0
            
            # Box 1
            x1 = MARGIN
            canvas.draw_text("CURRENT FILE", x1 + 4, canvas.page.y - 9, FONT_BOLD, 7.5)
            canvas.draw_text(str(block.get("current", "")), x1 + 4, canvas.page.y - 18, FONT_REGULAR, 7.5)
            
            canvas.draw_text("->", MARGIN + w + 4, canvas.page.y - 14, FONT_BOLD, 10)
            
            # Box 2
            x2 = MARGIN + w + 15
            canvas.draw_text("INDEXED CORPUS", x2 + 4, canvas.page.y - 9, FONT_BOLD, 7.5)
            canvas.draw_text(str(block.get("corpus", "")), x2 + 4, canvas.page.y - 18, FONT_REGULAR, 7.5)
            
            canvas.draw_text("->", MARGIN + 2 * w + 19, canvas.page.y - 14, FONT_BOLD, 10)
            
            # Box 3
            x3 = MARGIN + 2 * w + 30
            canvas.draw_text("EARLIEST KNOWN INSTANCE", x3 + 4, canvas.page.y - 9, FONT_BOLD, 7.5)
            canvas.draw_text(str(block.get("earliest", "")), x3 + 4, canvas.page.y - 18, FONT_REGULAR, 7.5)
            
            canvas.advance(26)
            canvas.draw_rule(canvas.page.y, width=0.5, grey=0.7)
            canvas.advance(6)
        elif kind == "spacer":
            canvas.advance(float(block.get("height", 8)))
        elif kind == "title":
            canvas.ensure(46)
            for line in wrap(block["text"], FONT_BOLD, 17, CONTENT_WIDTH):
                canvas.ensure(22)
                canvas.draw_text(line, MARGIN, canvas.page.y - 17, FONT_BOLD, 17)
                canvas.advance(21)
            if block.get("subtitle"):
                for line in wrap(block["subtitle"], FONT_REGULAR, 10, CONTENT_WIDTH):
                    canvas.ensure(14)
                    canvas.draw_text(line, MARGIN, canvas.page.y - 10, FONT_REGULAR, 10)
                    canvas.advance(13)
            canvas.advance(4)
            canvas.draw_rule(canvas.page.y, width=1.1, grey=0.15)
            canvas.advance(12)
        elif kind == "heading":
            canvas.ensure(22)
            canvas.advance(4)
            canvas.draw_text(block["text"], MARGIN, canvas.page.y - 10, FONT_BOLD, 10)
            canvas.advance(12)
            canvas.draw_rule(canvas.page.y, width=0.5, grey=0.5)
            canvas.advance(4)
        elif kind == "subheading":
            canvas.ensure(18)
            canvas.advance(3)
            canvas.draw_text(block["text"], MARGIN, canvas.page.y - 9, FONT_BOLD, 9)
            canvas.advance(11)
        elif kind == "paragraph":
            font = FONT_MONO if block.get("mono") else FONT_REGULAR
            size = float(block.get("size", 8.0))
            canvas.draw_lines(
                wrap(block["text"], font, size, CONTENT_WIDTH),
                x=MARGIN,
                font=font,
                size=size,
                leading=size + 2.5,
            )
            canvas.advance(2)
        elif kind == "bullets":
            for item in block.get("items", []):
                lines = wrap(str(item), FONT_REGULAR, 8.0, CONTENT_WIDTH - 14)
                for number, line in enumerate(lines):
                    canvas.ensure(10.5)
                    if number == 0:
                        canvas.draw_text(
                            "-", MARGIN, canvas.page.y - 8, FONT_REGULAR, 8.0
                        )
                    canvas.draw_text(
                        line, MARGIN + 14, canvas.page.y - 8, FONT_REGULAR, 8.0
                    )
                    canvas.advance(10.5)
            canvas.advance(2)
        elif kind == "kv":
            _render_kv(canvas, block)
        elif kind == "table":
            _render_table(canvas, block)

    canvas.footer(footer)
    return canvas.to_bytes(
        title=title, author=author, subject=subject, created=created
    ), canvas.page_count


def _render_kv(canvas: Canvas, block: dict[str, Any]) -> None:
    key_width = float(block.get("key_width", KEY_COLUMN))
    value_x = MARGIN + key_width + 6
    value_width = PAGE_WIDTH - MARGIN - value_x

    for row in block.get("rows", []):
        label, value = row[0], row[1]
        mono = bool(row[2]) if len(row) > 2 else False
        font = FONT_MONO if mono else FONT_REGULAR
        size = 7.5 if mono else 8.5

        label_lines = wrap(str(label), FONT_BOLD, 8.5, key_width)
        value_lines = wrap("--" if value in (None, "") else str(value), font, size,
                           value_width)
        height = max(len(label_lines), len(value_lines)) * 11.0
        canvas.ensure(height)
        top = canvas.page.y

        for index, line in enumerate(label_lines):
            canvas.draw_text(line, MARGIN, top - 8.5 - index * 11.0, FONT_BOLD, 8.5)
        for index, line in enumerate(value_lines):
            canvas.draw_text(line, value_x, top - 8.5 - index * 11.0, font, size)
        canvas.advance(height + 1.5)
    canvas.advance(3)


def _render_table(canvas: Canvas, block: dict[str, Any]) -> None:
    columns = [str(c) for c in block.get("columns", [])]
    rows = block.get("rows", [])
    if not columns:
        return

    weights = block.get("widths") or [1.0] * len(columns)
    total = sum(weights) or 1.0
    widths = [CONTENT_WIDTH * w / total for w in weights]
    xs, cursor = [], MARGIN
    for width in widths:
        xs.append(cursor)
        cursor += width

    mono_columns = set(block.get("mono_columns", []))
    size = float(block.get("size", 7.5))

    def draw_header() -> None:
        canvas.ensure(24)
        top = canvas.page.y
        for index, column in enumerate(columns):
            for line_no, line in enumerate(
                wrap(column, FONT_BOLD, size, widths[index] - 4)
            ):
                canvas.draw_text(
                    line, xs[index], top - size - line_no * (size + 2), FONT_BOLD, size
                )
        canvas.advance(size + 6)
        canvas.draw_rule(canvas.page.y, width=0.5)
        canvas.advance(4)

    draw_header()
    for row in rows:
        cells = []
        for index in range(len(columns)):
            raw = row[index] if index < len(row) else ""
            text = "--" if raw in (None, "") else str(raw)
            font = FONT_MONO if index in mono_columns else FONT_REGULAR
            cells.append((font, wrap(text, font, size, widths[index] - 4)))
        height = max(len(lines) for _, lines in cells) * (size + 2.5)

        if canvas.page.y - height < BOTTOM_LIMIT:
            canvas.new_page()
            draw_header()

        top = canvas.page.y
        for index, (font, lines) in enumerate(cells):
            for line_no, line in enumerate(lines):
                canvas.draw_text(
                    line,
                    xs[index],
                    top - size - line_no * (size + 2.5),
                    font,
                    size,
                )
        canvas.advance(height + 2.0)
    canvas.advance(4)
