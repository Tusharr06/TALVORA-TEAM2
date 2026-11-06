"""Synthetic, harmless PDF/DOCX fixtures for P07 extraction tests.

Every byte here is generated in-process from literal strings -- no real resumes, no
real candidate data, no network access. `reportlab` (a dev-only test dependency) is
used purely to produce genuinely parseable, real-text PDF pages (pypdf cannot author
pages, only read/merge them); `pypdfium2` + Pillow produce genuinely image-only
"scanned" pages with no text layer at all, for the real-OCR tests.
"""

from __future__ import annotations

import io
import zipfile

import docx
import pypdfium2 as pdfium
from PIL import Image, ImageDraw, ImageFont
from pypdf import PdfReader, PdfWriter
from reportlab.lib.pagesizes import LETTER
from reportlab.pdfgen import canvas


def build_digital_pdf(page_texts: list[str], line_height: float = 16.0) -> bytes:
    """A real, multi-page PDF with genuine selectable text per page (via reportlab).

    Each page's text is one string; `\\n`-separated lines are drawn top-to-bottom.
    """
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=LETTER)
    width, height = LETTER
    for page_text in page_texts:
        y = height - 72
        for line in page_text.split("\n"):
            c.drawString(72, y, line)
            y -= line_height
        c.showPage()
    c.save()
    return buf.getvalue()


def build_multicolumn_pdf_page(left_lines: list[str], right_lines: list[str]) -> bytes:
    """One page with two text columns at different x offsets (reading-order fixture)."""
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=LETTER)
    width, height = LETTER
    y = height - 72
    for line in left_lines:
        c.drawString(72, y, line)
        y -= 16
    y = height - 72
    for line in right_lines:
        c.drawString(width / 2 + 20, y, line)
        y -= 16
    c.showPage()
    c.save()
    return buf.getvalue()


def build_corrupted_pdf() -> bytes:
    """A PDF-signature-prefixed blob that is not a structurally valid PDF body."""
    return b"%PDF-1.4\nthis is not a real pdf body at all, just garbage bytes\n%%EOF"


def build_encrypted_pdf(text: str = "secret resume text") -> bytes:
    """A real, password-protected PDF (defense-in-depth unit coverage; P06 already
    rejects `/Encrypt` at upload, so this never reaches the worker in production)."""
    digital = build_digital_pdf([text])
    reader = PdfReader(io.BytesIO(digital))
    writer = PdfWriter()
    for page in reader.pages:
        writer.add_page(page)
    writer.encrypt(user_password="hunter2")
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def build_scanned_pdf_page(
    text: str,
    image_size: tuple[int, int] = (1200, 300),
    rotation: int = 0,
    font_size: int = 36,
    low_quality: bool = False,
) -> bytes:
    """A one-page, image-only PDF (no text layer) with rendered text -- a real
    "scanned" page for the mandated real-Tesseract-OCR tests.

    `low_quality=True` renders tiny, blurred, low-contrast text to deterministically
    produce a low OCR confidence heuristic.
    """
    if low_quality:
        image_size = (max(image_size[0] // 6, 180), max(image_size[1] // 6, 60))
        font_size = max(font_size // 6, 6)

    img = Image.new("L", image_size, color=255)
    draw = ImageDraw.Draw(img)
    font: ImageFont.FreeTypeFont | ImageFont.ImageFont
    try:
        font = ImageFont.truetype("arial.ttf", font_size)
    except OSError:
        font = ImageFont.load_default()
    fill = 140 if low_quality else 0  # low contrast gray vs. crisp black
    draw.text((10, 10), text, fill=fill, font=font)

    if low_quality:
        # Add speckle noise to further degrade OCR confidence deterministically.
        import random

        rng = random.Random(42)
        for _ in range(image_size[0] * image_size[1] // 8):
            x = rng.randrange(image_size[0])
            y = rng.randrange(image_size[1])
            draw.point((x, y), fill=rng.randrange(0, 256))

    pdf = pdfium.PdfDocument.new()
    pdfium_image = pdfium.PdfImage.new(pdf)
    pdfium_image.set_bitmap(pdfium.PdfBitmap.from_pil(img))
    width, height = img.size
    page = pdf.new_page(width, height)
    matrix = pdfium.PdfMatrix().scale(width, height)
    pdfium_image.set_matrix(matrix)
    page.insert_obj(pdfium_image)
    page.gen_content()
    if rotation:
        page.set_rotation(rotation)
    out = io.BytesIO()
    pdf.save(out)
    return out.getvalue()


def merge_pdfs(pdf_bytes_list: list[bytes]) -> bytes:
    """Concatenate several single/multi-page PDFs into one, preserving page order."""
    writer = PdfWriter()
    for pdf_bytes in pdf_bytes_list:
        reader = PdfReader(io.BytesIO(pdf_bytes))
        for page in reader.pages:
            writer.add_page(page)
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def build_many_page_digital_pdf(page_count: int, text_prefix: str = "Page") -> bytes:
    return build_digital_pdf(
        [
            f"{text_prefix} {i + 1} of {page_count} with real digital text content."
            for i in range(page_count)
        ]
    )


# ============================================================================
# DOCX fixtures
# ============================================================================


def build_docx(
    paragraphs: list[str] | None = None,
    table_rows: list[list[str]] | None = None,
    nested_table_rows: list[list[str]] | None = None,
    header_text: str | None = None,
    footer_text: str | None = None,
) -> bytes:
    """A real DOCX with python-docx: body paragraphs, an optional table (with an
    optional nested table inside its first cell), and an optional header/footer."""
    document = docx.Document()
    for para in paragraphs or []:
        document.add_paragraph(para)

    if table_rows:
        rows = len(table_rows)
        cols = len(table_rows[0])
        table = document.add_table(rows=rows, cols=cols)
        for r, row in enumerate(table_rows):
            for c, cell_text in enumerate(row):
                table.cell(r, c).text = cell_text
        if nested_table_rows:
            nested = table.cell(0, 0).add_table(
                rows=len(nested_table_rows), cols=len(nested_table_rows[0])
            )
            for r, row in enumerate(nested_table_rows):
                for c, cell_text in enumerate(row):
                    nested.cell(r, c).text = cell_text

    if header_text is not None:
        document.sections[0].header.paragraphs[0].text = header_text
    if footer_text is not None:
        document.sections[0].footer.paragraphs[0].text = footer_text

    buf = io.BytesIO()
    document.save(buf)
    return buf.getvalue()


def build_docx_with_textbox(body_text: str = "Visible body text") -> bytes:
    """A real DOCX whose `word/document.xml` contains a `w:txbxContent` element --
    an unsupported-layout feature this phase's walker deliberately does not visit."""
    data = build_docx(paragraphs=[body_text])
    return _inject_into_document_xml(
        data,
        insertion=(
            "<w:p><w:r><w:pict><v:shape><v:textbox><w:txbxContent>"
            "<w:p><w:r><w:t>Hidden textbox text</w:t></w:r></w:p>"
            "</w:txbxContent></v:textbox></v:shape></w:pict></w:r></w:p>"
        ),
    )


def build_docx_with_tracked_changes(body_text: str = "Visible body text") -> bytes:
    """A real DOCX whose `word/document.xml` contains `w:ins`/`w:del` elements."""
    data = build_docx(paragraphs=[body_text])
    return _inject_into_document_xml(
        data,
        insertion=(
            '<w:p><w:ins w:id="1" w:author="tester" w:date="2026-01-01T00:00:00Z">'
            "<w:r><w:t>inserted text</w:t></w:r></w:ins>"
            '<w:del w:id="2" w:author="tester" w:date="2026-01-01T00:00:00Z">'
            "<w:r><w:delText>deleted text</w:delText></w:r></w:del></w:p>"
        ),
    )


def _inject_into_document_xml(docx_bytes: bytes, insertion: str) -> bytes:
    """Rewrite `word/document.xml` inside a real DOCX zip, inserting `insertion`
    immediately before the closing `</w:body>` tag. Every other zip entry is copied
    byte-for-byte unchanged."""
    src = zipfile.ZipFile(io.BytesIO(docx_bytes))
    out_buf = io.BytesIO()
    with zipfile.ZipFile(out_buf, "w", zipfile.ZIP_DEFLATED) as dst:
        for item in src.infolist():
            content = src.read(item.filename)
            if item.filename == "word/document.xml":
                text = content.decode("utf-8")
                text = text.replace("</w:body>", insertion + "</w:body>")
                content = text.encode("utf-8")
            dst.writestr(item, content)
    return out_buf.getvalue()


def build_corrupted_docx() -> bytes:
    """Not a valid zip container at all."""
    return b"PK\x03\x04this is not a real zip/docx, just garbage bytes"


__all__ = [
    "build_corrupted_docx",
    "build_corrupted_pdf",
    "build_digital_pdf",
    "build_docx",
    "build_docx_with_textbox",
    "build_docx_with_tracked_changes",
    "build_encrypted_pdf",
    "build_many_page_digital_pdf",
    "build_multicolumn_pdf_page",
    "build_scanned_pdf_page",
    "merge_pdfs",
]
