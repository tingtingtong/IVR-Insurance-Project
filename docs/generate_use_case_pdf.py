"""Convert IVR_Use_Cases_and_Call_Flow.md to PDF using fpdf2."""
import re
from pathlib import Path
from fpdf import FPDF

MD_PATH = Path(__file__).parent / "IVR_Use_Cases_and_Call_Flow.md"
PDF_PATH = Path(__file__).parent / "IVR_Use_Cases_and_Call_Flow.pdf"


def sanitize(text):
    """Replace unicode chars that latin-1 can't encode."""
    return (text
            .replace("\u2014", "--")
            .replace("\u2013", "-")
            .replace("\u2018", "'")
            .replace("\u2019", "'")
            .replace("\u201c", '"')
            .replace("\u201d", '"')
            .replace("\u2022", "-")
            .replace("\u2026", "...")
            .replace("\u00a0", " ")
            .encode("latin-1", errors="replace")
            .decode("latin-1"))


class PDF(FPDF):
    def header(self):
        if self.page_no() > 1:
            self.set_font("Helvetica", "I", 8)
            self.set_text_color(130, 130, 130)
            self.cell(0, 5, "US Insurance Company (UIC) IVR -- Use Cases & Call Flow", align="C")
            self.ln(8)

    def footer(self):
        self.set_y(-15)
        self.set_font("Helvetica", "I", 8)
        self.set_text_color(130, 130, 130)
        self.cell(0, 10, f"Page {self.page_no()}/{{nb}}", align="C")

    def section_title(self, title, level=1):
        sizes = {1: 16, 2: 13, 3: 11, 4: 10}
        size = sizes.get(level, 10)
        self.set_font("Helvetica", "B", size)
        self.set_text_color(20, 60, 120)
        if level <= 2:
            self.ln(4)
        self.multi_cell(0, size * 0.55, sanitize(title))
        self.set_text_color(0, 0, 0)
        if level <= 2:
            self.set_draw_color(20, 60, 120)
            self.line(self.l_margin, self.get_y(), self.w - self.r_margin, self.get_y())
            self.ln(3)
        else:
            self.ln(2)

    def body_text(self, text):
        self.set_font("Helvetica", "", 9)
        # Process inline bold and italic
        self._write_rich_text(text)
        self.ln(3)

    def bullet_point(self, text, indent=0):
        self.set_font("Helvetica", "", 9)
        x = self.l_margin + 5 + indent
        self.set_x(x)
        self.cell(4, 5, "-")
        self._write_rich_text(text, w=self.w - x - self.r_margin - 4)
        self.ln(2)

    def code_block(self, text):
        self.set_font("Courier", "", 7.5)
        self.set_fill_color(245, 245, 245)
        self.set_draw_color(200, 200, 200)
        x = self.l_margin + 3
        w = self.w - self.l_margin - self.r_margin - 6
        lines = text.split("\n")
        # Calculate height
        line_h = 3.8
        block_h = len(lines) * line_h + 4
        # Check if we need a page break
        if self.get_y() + block_h > self.h - self.b_margin:
            self.add_page()
        y_start = self.get_y()
        self.rect(x, y_start, w, block_h, "DF")
        self.set_xy(x + 2, y_start + 2)
        for line in lines:
            self.set_x(x + 2)
            self.cell(w - 4, line_h, sanitize(line), ln=1)
        self.ln(3)

    def table_row(self, cells, widths, header=False):
        self.set_font("Helvetica", "B" if header else "", 8)
        if header:
            self.set_fill_color(20, 60, 120)
            self.set_text_color(255, 255, 255)
        else:
            self.set_fill_color(250, 250, 250)
            self.set_text_color(0, 0, 0)

        max_lines = 1
        cell_texts = []
        for i, cell in enumerate(cells):
            cell = re.sub(r"\*\*(.*?)\*\*", r"\1", cell)  # strip bold markers
            cell = sanitize(cell.strip())
            cell_texts.append(cell)
            # Estimate lines needed
            lines_needed = max(1, int(len(cell) / (widths[i] / 2)) + 1)
            max_lines = max(max_lines, lines_needed)

        row_h = max(6, max_lines * 4.5)

        # Check page break
        if self.get_y() + row_h > self.h - self.b_margin:
            self.add_page()

        y_start = self.get_y()
        x_start = self.get_x()

        for i, cell in enumerate(cell_texts):
            x = x_start + sum(widths[:i])
            self.set_xy(x, y_start)
            self.multi_cell(widths[i], 4.5, cell, border=1, fill=True, align="L")

        # Move to after the tallest cell
        max_y = max(self.get_y(), y_start + row_h)
        self.set_y(max(self.get_y(), y_start + 4.5))

    def _write_rich_text(self, text, w=0):
        """Write text with inline **bold** and *italic* support."""
        if w == 0:
            w = self.w - self.l_margin - self.r_margin
        # Simple approach: strip markdown formatting for PDF
        text = re.sub(r"\*\*(.*?)\*\*", r"\1", text)
        text = re.sub(r"\*(.*?)\*", r"\1", text)
        text = re.sub(r"`(.*?)`", r"\1", text)
        self.multi_cell(w, 4.5, sanitize(text))


def parse_table(lines):
    """Parse markdown table lines into headers and rows."""
    rows = []
    for line in lines:
        line = line.strip()
        if line.startswith("|") and not re.match(r"^\|[\s\-\|:]+\|$", line):
            cells = [c.strip() for c in line.split("|")[1:-1]]
            rows.append(cells)
    if len(rows) < 2:
        return None, None
    return rows[0], rows[1:]


def compute_col_widths(headers, rows, total_w):
    """Compute proportional column widths based on content length."""
    n = len(headers)
    max_lens = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            if i < n:
                max_lens[i] = max(max_lens[i], len(cell))
    total_len = sum(max_lens) or 1
    widths = [(ml / total_len) * total_w for ml in max_lens]
    # Ensure minimum width
    widths = [max(w, 15) for w in widths]
    # Scale to fit
    scale = total_w / sum(widths)
    widths = [w * scale for w in widths]
    return widths


def build_pdf():
    md = MD_PATH.read_text(encoding="utf-8")
    lines = md.split("\n")

    pdf = PDF()
    pdf.alias_nb_pages()
    pdf.set_auto_page_break(auto=True, margin=20)
    pdf.add_page()

    # Title page
    pdf.set_font("Helvetica", "B", 22)
    pdf.set_text_color(20, 60, 120)
    pdf.ln(30)
    pdf.cell(0, 12, "US Insurance Company (UIC)", align="C", ln=True)
    pdf.set_font("Helvetica", "B", 18)
    pdf.cell(0, 10, "IVR Use Cases & Call Flow", align="C", ln=True)
    pdf.ln(10)
    pdf.set_font("Helvetica", "", 11)
    pdf.set_text_color(80, 80, 80)
    pdf.cell(0, 8, "Voice IVR System Documentation", align="C", ln=True)
    pdf.ln(5)
    pdf.set_draw_color(20, 60, 120)
    pdf.line(60, pdf.get_y(), pdf.w - 60, pdf.get_y())
    pdf.ln(60)
    pdf.set_font("Helvetica", "I", 10)
    pdf.set_text_color(100, 100, 100)
    from datetime import date
    pdf.cell(0, 6, f"Generated: {date.today().strftime('%B %d, %Y')}", align="C", ln=True)
    pdf.add_page()

    i = 0
    in_code = False
    code_lines = []
    in_table = False
    table_lines = []

    while i < len(lines):
        line = lines[i]

        # Code block toggle
        if line.strip().startswith("```"):
            if in_code:
                pdf.code_block("\n".join(code_lines))
                code_lines = []
                in_code = False
            else:
                # Flush any pending table
                if in_table:
                    _flush_table(pdf, table_lines)
                    table_lines = []
                    in_table = False
                in_code = True
            i += 1
            continue

        if in_code:
            code_lines.append(line)
            i += 1
            continue

        # Table detection
        if line.strip().startswith("|") and "|" in line.strip()[1:]:
            if not in_table:
                in_table = True
                table_lines = []
            table_lines.append(line)
            i += 1
            continue
        elif in_table:
            _flush_table(pdf, table_lines)
            table_lines = []
            in_table = False

        stripped = line.strip()

        # Horizontal rule
        if stripped == "---":
            pdf.ln(2)
            pdf.set_draw_color(180, 180, 180)
            pdf.line(pdf.l_margin, pdf.get_y(), pdf.w - pdf.r_margin, pdf.get_y())
            pdf.ln(3)
            i += 1
            continue

        # Headers
        if stripped.startswith("#"):
            match = re.match(r"^(#{1,4})\s+(.*)", stripped)
            if match:
                level = len(match.group(1))
                title = match.group(2)
                title = re.sub(r"\*\*(.*?)\*\*", r"\1", title)
                pdf.section_title(title, level)
                i += 1
                continue

        # Bullet points
        if stripped.startswith("- "):
            text = stripped[2:]
            indent = len(line) - len(line.lstrip())
            pdf.bullet_point(text, indent=indent)
            i += 1
            continue

        # Empty line
        if not stripped:
            pdf.ln(2)
            i += 1
            continue

        # Regular text
        pdf.body_text(stripped)
        i += 1

    # Flush remaining
    if in_table:
        _flush_table(pdf, table_lines)
    if in_code:
        pdf.code_block("\n".join(code_lines))

    pdf.output(str(PDF_PATH))
    print(f"PDF generated: {PDF_PATH}")


def _flush_table(pdf, table_lines):
    headers, rows = parse_table(table_lines)
    if headers and rows:
        total_w = pdf.w - pdf.l_margin - pdf.r_margin
        widths = compute_col_widths(headers, rows, total_w)
        pdf.table_row(headers, widths, header=True)
        for row in rows:
            # Pad row if fewer cells than headers
            while len(row) < len(headers):
                row.append("")
            pdf.table_row(row[:len(headers)], widths)
        pdf.ln(3)


if __name__ == "__main__":
    build_pdf()
