"""Controlled, Zero-Network DOCX and PDF Pack Exporters (P16).

Security invariants:
- Renderer never fetches external HTTP(S)/file/data URLs, scripts, embedded objects,
  or unreviewed external fonts.
- All text inputs pass `assert_safe_for_renderer` and XML/HTML escaping (`xml.sax.saxutils.escape`).
- Bounded wall-clock budget and deterministic cleanup of in-memory/temp buffers in `finally`.
- Never embeds secrets, raw S3 keys/URLs, or unrelated internal application notes.
"""

import html
import io
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from docx import Document as CreateDocxDocument
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    KeepTogether,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from talvora.modules.feedback.domain.models import (
    EXPORT_REVOCATION_DISCLOSURE,
    assert_safe_for_renderer,
)
from talvora.shared.transport.problems import ProblemException

_ALLOWLISTED_REGULAR_FONT_PATHS = (Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),)
_ALLOWLISTED_BOLD_FONT_PATHS = (Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),)
_FONT_REGISTERED = False
_BODY_FONT = "Helvetica"
_BOLD_FONT = "Helvetica-Bold"


def _ensure_allowlisted_fonts() -> tuple[str, str]:
    """Registers bundled allowlisted local DejaVuSans fonts once for full Unicode support."""
    global _FONT_REGISTERED, _BODY_FONT, _BOLD_FONT
    if _FONT_REGISTERED:
        return _BODY_FONT, _BOLD_FONT

    for reg_path, bold_path in zip(
        _ALLOWLISTED_REGULAR_FONT_PATHS, _ALLOWLISTED_BOLD_FONT_PATHS, strict=True
    ):
        if reg_path.is_file() and bold_path.is_file():
            pdfmetrics.registerFont(TTFont("TalvoraSans", str(reg_path)))
            pdfmetrics.registerFont(TTFont("TalvoraSans-Bold", str(bold_path)))
            _BODY_FONT = "TalvoraSans"
            _BOLD_FONT = "TalvoraSans-Bold"
            break

    _FONT_REGISTERED = True
    return _BODY_FONT, _BOLD_FONT


@dataclass(frozen=True)
class ExportCitationItem:
    fragment_id: UUID
    locator_label: str
    quote: str


@dataclass(frozen=True)
class ExportFeedbackItem:
    interviewer_label: str
    revision_number: int
    submitted_at_iso: str
    human_recommendation: str
    overall_observations: str
    ratings: tuple[tuple[str, str, str], ...]  # (competency_code, rating, observation)


@dataclass(frozen=True)
class PackExportPayload:
    """Sanitized, self-contained view model passed to DOCX and PDF renderers."""

    job_title: str
    candidate_display_name: str
    stage: str
    duration_minutes: int
    pack_revision_id: UUID
    pack_revision_number: int
    framework_version_id: UUID
    requirement_version_id: UUID
    document_version_id: UUID | None
    published_by: UUID | None
    published_at: datetime | None
    generated_at: datetime
    candidate_overview: str
    candidate_evidence_summary: str
    interviewer_instructions: str
    notes_guidance: str
    time_allocation: dict[str, Any]
    questions: list[dict[str, Any]]
    rubric: list[dict[str, Any]]
    citations: list[ExportCitationItem]
    feedback_items: list[ExportFeedbackItem]
    max_render_seconds: float = 15.0


def _check_deadline(started_at: float, max_seconds: float) -> None:
    if (time.monotonic() - started_at) > max_seconds:
        raise ProblemException(
            status_code=503,
            title="Export Render Timeout",
            code="LIMIT_EXCEEDED",
            detail="Document export exceeded the bounded rendering time limit.",
        )


def _pdf_escape(raw: str) -> str:
    """Validates against external resource injection and escapes XML entities for ReportLab Paragraph."""
    safe = assert_safe_for_renderer(raw)
    return html.escape(safe, quote=True).replace("\n", "<br/>")


def render_pack_docx(payload: PackExportPayload) -> bytes:
    """Generates an editable, structured DOCX interview preparation guide with zero external links."""
    started = time.monotonic()
    buf = io.BytesIO()
    try:
        doc = CreateDocxDocument()
        _check_deadline(started, payload.max_render_seconds)

        # Header & Confidentiality Label
        section = doc.sections[0]
        header_para = section.header.paragraphs[0]
        header_para.text = "CONFIDENTIAL — TALVORA INTERVIEW PREPARATION PACK"
        footer_para = section.footer.paragraphs[0]
        footer_para.text = (
            f"Pack Rev #{payload.pack_revision_number} ({payload.pack_revision_id}) | "
            f"Generated {payload.generated_at.isoformat()}"
        )

        doc.add_heading(
            assert_safe_for_renderer(f"Interview Pack: {payload.job_title}"),
            level=0,
        )
        conf_p = doc.add_paragraph()
        conf_run = conf_p.add_run(EXPORT_REVOCATION_DISCLOSURE)
        conf_run.bold = True

        # Metadata Table
        doc.add_heading("1. Revision & Provenance Metadata", level=1)
        meta_rows = [
            ("Candidate", assert_safe_for_renderer(payload.candidate_display_name)),
            (
                "Stage & Duration",
                assert_safe_for_renderer(f"{payload.stage} ({payload.duration_minutes} min)"),
            ),
            ("Pack Revision", f"v{payload.pack_revision_number} ({payload.pack_revision_id})"),
            ("Framework Version ID", str(payload.framework_version_id)),
            ("Requirement Version ID", str(payload.requirement_version_id)),
            (
                "Bound Resume Version ID",
                str(payload.document_version_id) if payload.document_version_id else "None bound",
            ),
            (
                "Reviewed & Published By",
                f"{payload.published_by} at {payload.published_at.isoformat()}"
                if payload.published_by and payload.published_at
                else "Draft",
            ),
            ("Generated Timestamp (UTC)", payload.generated_at.isoformat()),
        ]
        meta_table = doc.add_table(rows=len(meta_rows), cols=2)
        for idx, (k, v) in enumerate(meta_rows):
            meta_table.rows[idx].cells[0].text = k
            meta_table.rows[idx].cells[1].text = v

        _check_deadline(started, payload.max_render_seconds)

        # Candidate Overview & Instructions
        doc.add_heading("2. Candidate Overview & Evidence Summary", level=1)
        doc.add_paragraph(assert_safe_for_renderer(payload.candidate_overview))
        doc.add_paragraph(assert_safe_for_renderer(payload.candidate_evidence_summary))

        doc.add_heading("3. Interviewer Instructions & Time Allocation", level=1)
        doc.add_paragraph(assert_safe_for_renderer(payload.interviewer_instructions))
        ta = payload.time_allocation
        alloc_table = doc.add_table(rows=2, cols=4)
        headers = ["Introduction", "Structured Questions", "Candidate Q&A", "Closing"]
        vals = [
            f"{ta.get('intro_minutes', 0)} min",
            f"{ta.get('questions_minutes', 0)} min",
            f"{ta.get('candidate_questions_minutes', 0)} min",
            f"{ta.get('closing_minutes', 0)} min",
        ]
        for col_i in range(4):
            alloc_table.rows[0].cells[col_i].text = headers[col_i]
            alloc_table.rows[1].cells[col_i].text = vals[col_i]

        # Structured Questions Table (editable with dedicated Notes column)
        doc.add_heading("4. Structured Interview Questions & Editable Notes", level=1)
        q_table = doc.add_table(rows=len(payload.questions) + 1, cols=5)
        q_headers = [
            "#",
            "Category / Premise",
            "Minutes",
            "Question & Expected Signals",
            "Interviewer Live Notes",
        ]
        for c_idx, h in enumerate(q_headers):
            q_table.rows[0].cells[c_idx].text = h

        for idx, q in enumerate(payload.questions, start=1):
            _check_deadline(started, payload.max_render_seconds)
            row_cells = q_table.rows[idx].cells
            row_cells[0].text = str(idx)
            row_cells[1].text = assert_safe_for_renderer(
                f"{q.get('category', '')}\n[{q.get('premise_classification', '')}]"
            )
            row_cells[2].text = f"{q.get('allocated_minutes', 0)} min"
            signals = "\n".join(
                f"- {assert_safe_for_renderer(str(s))}" for s in (q.get("expected_signals") or [])
            )
            row_cells[3].text = (
                f"{assert_safe_for_renderer(str(q.get('prompt_text', '')))}\n\n"
                f"Expected observable signals:\n{signals}"
            )
            row_cells[4].text = "[Editable space for verbatim interview observations]"

        # Competency Rubric Table
        doc.add_heading("5. Observable Competency Rubric (1-4 & NOT_ASSESSED)", level=1)
        doc.add_paragraph(assert_safe_for_renderer(payload.notes_guidance))
        r_table = doc.add_table(rows=len(payload.rubric) + 1, cols=6)
        r_headers = ["Competency", "1", "2", "3", "4", "NOT_ASSESSED"]
        for c_idx, h in enumerate(r_headers):
            r_table.rows[0].cells[c_idx].text = h
        for idx, comp in enumerate(payload.rubric, start=1):
            anchors = comp.get("anchors") or {}
            rc = r_table.rows[idx].cells
            rc[0].text = assert_safe_for_renderer(
                f"{comp.get('code', '')}: {comp.get('name', '')}\n{comp.get('definition', '')}"
            )
            for col_i, key in enumerate(("1", "2", "3", "4", "NOT_ASSESSED"), start=1):
                rc[col_i].text = assert_safe_for_renderer(str(anchors.get(key, "")))

        # Source Citations Appendix
        doc.add_heading("6. Pinned Source Evidence Appendix", level=1)
        if payload.citations:
            for cit in payload.citations:
                doc.add_paragraph(
                    assert_safe_for_renderer(
                        f'[{cit.fragment_id}] ({cit.locator_label}): "{cit.quote}"'
                    )
                )
        else:
            doc.add_paragraph("No candidate-specific resume excerpts cited in this pack revision.")

        # Optional Authorized Submitted Feedback Section
        if payload.feedback_items:
            doc.add_heading("7. Authorized Submitted Interviewer Feedback", level=1)
            for fb in payload.feedback_items:
                doc.add_paragraph(
                    assert_safe_for_renderer(
                        f"{fb.interviewer_label} | Rev #{fb.revision_number} | "
                        f"Submitted: {fb.submitted_at_iso} | Stance: {fb.human_recommendation}"
                    )
                )
                doc.add_paragraph(assert_safe_for_renderer(fb.overall_observations))
                for code, rating_val, obs in fb.ratings:
                    doc.add_paragraph(assert_safe_for_renderer(f"  - {code}: {rating_val} — {obs}"))

        _check_deadline(started, payload.max_render_seconds)
        doc.save(buf)
        return buf.getvalue()
    finally:
        buf.close()


def render_pack_pdf(payload: PackExportPayload) -> bytes:
    """Generates a readable, multi-page PDF with wrapped tables, Unicode support, and zero network I/O."""
    started = time.monotonic()
    body_font, bold_font = _ensure_allowlisted_fonts()
    buf = io.BytesIO()
    try:
        styles = getSampleStyleSheet()
        title_style = ParagraphStyle(
            "TalvoraTitle",
            parent=styles["Heading1"],
            fontName=bold_font,
            fontSize=15,
            leading=19,
            spaceAfter=6,
        )
        h2_style = ParagraphStyle(
            "TalvoraH2",
            parent=styles["Heading2"],
            fontName=bold_font,
            fontSize=11.5,
            leading=15,
            spaceBefore=10,
            spaceAfter=4,
        )
        body_style = ParagraphStyle(
            "TalvoraBody",
            parent=styles["Normal"],
            fontName=body_font,
            fontSize=9,
            leading=12.5,
            wordWrap="CJK",
        )
        small_bold_style = ParagraphStyle(
            "TalvoraSmallBold",
            parent=body_style,
            fontName=bold_font,
            fontSize=8.5,
            leading=11.5,
        )

        story: list[Any] = []
        story.append(Paragraph(_pdf_escape(f"Interview Pack: {payload.job_title}"), title_style))
        story.append(Paragraph(_pdf_escape(EXPORT_REVOCATION_DISCLOSURE), small_bold_style))
        story.append(Spacer(1, 4 * mm))

        # 1. Metadata Table
        story.append(Paragraph("1. Revision &amp; Provenance Metadata", h2_style))
        meta_data = [
            [
                Paragraph("<b>Candidate</b>", small_bold_style),
                Paragraph(_pdf_escape(payload.candidate_display_name), body_style),
            ],
            [
                Paragraph("<b>Stage &amp; Duration</b>", small_bold_style),
                Paragraph(
                    _pdf_escape(f"{payload.stage} ({payload.duration_minutes} min)"),
                    body_style,
                ),
            ],
            [
                Paragraph("<b>Pack Revision</b>", small_bold_style),
                Paragraph(
                    _pdf_escape(f"v{payload.pack_revision_number} ({payload.pack_revision_id})"),
                    body_style,
                ),
            ],
            [
                Paragraph("<b>Framework / Requirement</b>", small_bold_style),
                Paragraph(
                    _pdf_escape(
                        f"Framework: {payload.framework_version_id} | Req: {payload.requirement_version_id}"
                    ),
                    body_style,
                ),
            ],
            [
                Paragraph("<b>Bound Resume Version</b>", small_bold_style),
                Paragraph(
                    _pdf_escape(
                        str(payload.document_version_id)
                        if payload.document_version_id
                        else "None bound"
                    ),
                    body_style,
                ),
            ],
            [
                Paragraph("<b>Reviewer &amp; Generated</b>", small_bold_style),
                Paragraph(
                    _pdf_escape(
                        f"Reviewer: {payload.published_by or 'Draft'} | "
                        f"Generated: {payload.generated_at.isoformat()}"
                    ),
                    body_style,
                ),
            ],
        ]
        meta_tbl = Table(meta_data, colWidths=[48 * mm, 128 * mm])
        meta_tbl.setStyle(
            TableStyle(
                [
                    ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#CBD5E1")),
                    ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#F8FAFC")),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("PADDING", (0, 0), (-1, -1), 4),
                ]
            )
        )
        story.append(meta_tbl)

        _check_deadline(started, payload.max_render_seconds)

        # 2. Overview & Instructions
        story.append(Paragraph("2. Candidate Overview &amp; Evidence Summary", h2_style))
        story.append(Paragraph(_pdf_escape(payload.candidate_overview), body_style))
        story.append(Spacer(1, 2 * mm))
        story.append(Paragraph(_pdf_escape(payload.candidate_evidence_summary), body_style))

        story.append(Paragraph("3. Interviewer Instructions &amp; Timing", h2_style))
        story.append(Paragraph(_pdf_escape(payload.interviewer_instructions), body_style))
        ta = payload.time_allocation
        story.append(
            Paragraph(
                _pdf_escape(
                    f"Time Budget — Intro: {ta.get('intro_minutes', 0)}m | "
                    f"Questions: {ta.get('questions_minutes', 0)}m | "
                    f"Candidate Q&A: {ta.get('candidate_questions_minutes', 0)}m | "
                    f"Closing: {ta.get('closing_minutes', 0)}m"
                ),
                small_bold_style,
            )
        )

        # 4. Questions Table
        story.append(Paragraph("4. Structured Interview Questions", h2_style))
        q_rows: list[list[Any]] = [
            [
                Paragraph("#", small_bold_style),
                Paragraph("Category / Provenance", small_bold_style),
                Paragraph("Min", small_bold_style),
                Paragraph("Question &amp; Expected Observable Signals", small_bold_style),
            ]
        ]
        for idx, q in enumerate(payload.questions, start=1):
            _check_deadline(started, payload.max_render_seconds)
            sigs = "<br/>".join(
                f"• {_pdf_escape(str(s))}" for s in (q.get("expected_signals") or [])
            )
            q_rows.append(
                [
                    Paragraph(str(idx), body_style),
                    Paragraph(
                        _pdf_escape(
                            f"{q.get('category', '')}\n[{q.get('premise_classification', '')}]"
                        ),
                        body_style,
                    ),
                    Paragraph(str(q.get("allocated_minutes", 0)), body_style),
                    Paragraph(
                        f"{_pdf_escape(str(q.get('prompt_text', '')))}<br/><br/>"
                        f"<i>Signals:</i><br/>{sigs}",
                        body_style,
                    ),
                ]
            )
        q_tbl = Table(q_rows, colWidths=[10 * mm, 38 * mm, 12 * mm, 116 * mm], repeatRows=1)
        q_tbl.setStyle(
            TableStyle(
                [
                    ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#CBD5E1")),
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#EEF2FF")),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("PADDING", (0, 0), (-1, -1), 4),
                ]
            )
        )
        story.append(q_tbl)

        # Page break before Rubric & Citations so multi-page structure is clean and unclipped
        story.append(PageBreak())
        story.append(
            Paragraph("5. Observable Competency Rubric (1-4 &amp; NOT_ASSESSED)", h2_style)
        )
        story.append(Paragraph(_pdf_escape(payload.notes_guidance), body_style))
        story.append(Spacer(1, 2 * mm))
        for comp in payload.rubric:
            _check_deadline(started, payload.max_render_seconds)
            anchors = comp.get("anchors") or {}
            comp_block = [
                Paragraph(
                    _pdf_escape(
                        f"{comp.get('code', '')} — {comp.get('name', '')}: {comp.get('definition', '')}"
                    ),
                    small_bold_style,
                )
            ]
            anchor_rows = [
                [
                    Paragraph(_pdf_escape(k), small_bold_style),
                    Paragraph(_pdf_escape(str(anchors.get(k, ""))), body_style),
                ]
                for k in ("1", "2", "3", "4", "NOT_ASSESSED")
            ]
            atbl = Table(anchor_rows, colWidths=[28 * mm, 148 * mm])
            atbl.setStyle(
                TableStyle(
                    [
                        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#CBD5E1")),
                        ("VALIGN", (0, 0), (-1, -1), "TOP"),
                        ("PADDING", (0, 0), (-1, -1), 3),
                    ]
                )
            )
            comp_block.append(atbl)
            comp_block.append(Spacer(1, 3 * mm))
            story.append(KeepTogether(comp_block))

        story.append(Paragraph("6. Pinned Source Evidence Appendix", h2_style))
        if payload.citations:
            for cit in payload.citations:
                story.append(
                    Paragraph(
                        _pdf_escape(f'[{cit.fragment_id}] ({cit.locator_label}): "{cit.quote}"'),
                        body_style,
                    )
                )
        else:
            story.append(
                Paragraph(
                    "No candidate-specific resume excerpts cited in this pack revision.",
                    body_style,
                )
            )

        if payload.feedback_items:
            story.append(Paragraph("7. Authorized Submitted Interviewer Feedback", h2_style))
            for fb in payload.feedback_items:
                story.append(
                    Paragraph(
                        _pdf_escape(
                            f"{fb.interviewer_label} (Rev #{fb.revision_number}, "
                            f"{fb.submitted_at_iso}) — {fb.human_recommendation}: "
                            f"{fb.overall_observations}"
                        ),
                        body_style,
                    )
                )
                for comp_code, r_val, r_obs in fb.ratings:
                    obs_display = r_obs if r_obs else "Not assessed in this interview session."
                    story.append(
                        Paragraph(
                            _pdf_escape(f"  • [{comp_code}] Rating={r_val}: {obs_display}"),
                            body_style,
                        )
                    )

        def _draw_chrome(canvas_obj: Any, doc_obj: Any) -> None:
            canvas_obj.saveState()
            canvas_obj.setFont(body_font, 7.5)
            canvas_obj.drawString(
                17 * mm,
                288 * mm,
                "CONFIDENTIAL — TALVORA INTERVIEW PACK",
            )
            canvas_obj.drawString(
                17 * mm,
                10 * mm,
                f"Pack Rev #{payload.pack_revision_number} | Page {doc_obj.page}",
            )
            canvas_obj.restoreState()

        pdf_doc = SimpleDocTemplate(
            buf,
            pagesize=A4,
            leftMargin=17 * mm,
            rightMargin=17 * mm,
            topMargin=16 * mm,
            bottomMargin=16 * mm,
            title=f"Talvora Interview Pack v{payload.pack_revision_number}",
            author="Talvora Workspace",
        )
        _check_deadline(started, payload.max_render_seconds)
        pdf_doc.build(story, onFirstPage=_draw_chrome, onLaterPages=_draw_chrome)
        _check_deadline(started, payload.max_render_seconds)
        return buf.getvalue()
    finally:
        buf.close()
