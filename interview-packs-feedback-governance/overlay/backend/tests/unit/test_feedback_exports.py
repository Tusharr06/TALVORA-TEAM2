"""Unit tests for Structured Feedback validators and Controlled DOCX/PDF Exporters (P16)."""

import io
from datetime import UTC, datetime
from uuid import uuid4

import docx
import pypdf
import pypdfium2 as pdfium
import pytest

from talvora.modules.feedback.application.exporters import (
    ExportCitationItem,
    ExportFeedbackItem,
    PackExportPayload,
    render_pack_docx,
    render_pack_pdf,
)
from talvora.modules.feedback.domain.models import (
    EXPORT_REVOCATION_DISCLOSURE,
    assert_safe_for_renderer,
    validate_interview_observation_text,
    validate_rating_value,
)
from talvora.shared.transport.problems import ProblemException


def _sample_export_payload(
    *, include_feedback: bool = True, max_render_seconds: float = 15.0
) -> PackExportPayload:
    frag_id = uuid4()
    return PackExportPayload(
        job_title="Principal Distributed Systems Engineer — München",
        candidate_display_name="Zofia Żółć-Nguyễn (東京)",
        stage="TECHNICAL_DEEP_DIVE",
        duration_minutes=45,
        pack_revision_id=uuid4(),
        pack_revision_number=2,
        framework_version_id=uuid4(),
        requirement_version_id=uuid4(),
        document_version_id=uuid4(),
        published_by=uuid4(),
        published_at=datetime(2026, 11, 6, 10, 0, tzinfo=UTC),
        generated_at=datetime(2026, 11, 6, 12, 0, tzinfo=UTC),
        candidate_overview=(
            "Led PostgreSQL partitioning and transactional outbox migration handling 45k writes/sec."
        ),
        candidate_evidence_summary=(
            "Verified 8 years distributed backend engineering across high-throughput ledger systems."
        ),
        interviewer_instructions=(
            "Welcome Zofia Żółć; probe concrete operational metrics and failure-isolation invariants."
        ),
        notes_guidance="Record verbatim behavioral evidence per competency anchor; do not infer protected traits.",
        time_allocation={
            "intro_minutes": 5,
            "questions_minutes": 35,
            "candidate_questions_minutes": 5,
            "total_minutes": 45,
        },
        questions=[
            {
                "sequence_no": 1,
                "competency_code": "DIST_SYS",
                "question_type": "COMPETENCY",
                "prompt": "Walk through a production split-brain or queue backlog incident you resolved.",
                "follow_ups": [
                    "What telemetry alerted you first?",
                    "What invariant prevented silent data loss?",
                ],
                "expected_signal": "Fencing tokens, idempotency keys, and measurable SLO recovery.",
                "scoring_guidance": "Anchor 3+ requires concrete production trade-offs rather than textbook theory.",
                "allocated_minutes": 20,
                "is_evidence_supported": True,
                "rationale": "Directly probes claimed 45k writes/sec PostgreSQL migration.",
            },
            {
                "sequence_no": 2,
                "competency_code": "TECH_LEAD",
                "question_type": "BEHAVIORAL",
                "prompt": "Describe an architectural RFC where a peer team strongly disagreed.",
                "follow_ups": ["What experiment unlocked consensus?"],
                "expected_signal": "Empathetic stakeholder alignment grounded in benchmarks.",
                "scoring_guidance": "Distinguish personal facilitation from manager escalation.",
                "allocated_minutes": 15,
                "is_evidence_supported": False,
                "rationale": "Evaluates staff-level cross-squad influence.",
            },
        ],
        rubric=[
            {
                "code": "DIST_SYS",
                "name": "Distributed Systems Architecture",
                "description": "Evaluates partitioning, failure domains, and consistency trade-offs.",
                "anchors": {
                    "1": "Cannot identify single points of failure.",
                    "2": "Identifies basic retries without idempotency.",
                    "3": "Designs clear failure domains, outbox durability, and backpressure.",
                    "4": "Demonstrates multi-region trade-offs with concrete production metrics.",
                },
            },
            {
                "code": "TECH_LEAD",
                "name": "Cross-Team Technical Leadership",
                "description": "Evaluates RFC alignment and constructive disagreement.",
                "anchors": {
                    "1": "Dismisses counter-arguments without evidence.",
                    "2": "Communicates trade-offs within immediate squad only.",
                    "3": "Drives multi-team RFC consensus with written decision records.",
                    "4": "Resolves org-wide architectural deadlocks pragmatically.",
                },
            },
        ],
        citations=[
            ExportCitationItem(
                fragment_id=frag_id,
                locator_label="Resume p.1 L12-L18",
                quote="Designed sharded PostgreSQL outbox handling 45,000 writes/sec with zero lost events.",
            )
        ],
        feedback_items=(
            [
                ExportFeedbackItem(
                    interviewer_label="Interviewer 8f31a9c0",
                    revision_number=2,
                    submitted_at_iso="2026-11-06T11:30:00+00:00",
                    human_recommendation="ADVANCE_DISCUSSION",
                    overall_observations=(
                        "Clear articulation of fencing tokens and WAL lag mitigation; "
                        "leadership question not reached due to deep architecture dive."
                    ),
                    ratings=(
                        (
                            "DIST_SYS",
                            "4",
                            "Detailed fencing token and WAL lag mitigation under failover.",
                        ),
                        (
                            "TECH_LEAD",
                            "NOT_ASSESSED",
                            "",
                        ),
                    ),
                )
            ]
            if include_feedback
            else []
        ),
        max_render_seconds=max_render_seconds,
    )


def test_rating_scale_enforces_anchors_and_not_assessed_distinct_from_zero() -> None:
    for r in ("1", "2", "3", "4"):
        assert validate_rating_value(r) == r

    assert validate_rating_value("not_assessed") == "NOT_ASSESSED"
    assert validate_rating_value("NOT_ASSESSED") != "0"

    for invalid in ("0", "5", "-1", "STRONG_YES", ""):
        with pytest.raises(ProblemException) as exc_info:
            validate_rating_value(invalid)
        assert exc_info.value.status_code == 422


def test_renderer_and_observation_injection_defenses_block_urls_files_scripts_and_protected_traits() -> (
    None
):
    clean = validate_interview_observation_text(
        "Candidate explained WAL fencing tokens clearly — Zofia Żółć (東京).",
        field_label="observation",
    )
    assert "Zofia Żółć" in clean

    malicious_inputs = [
        '<img src="https://attacker.example/pixel.png">',
        "Read local secret file:///etc/passwd",
        "<script>alert('xss')</script>",
        '<iframe src="javascript:alert(1)"></iframe>',
        '=HYPERLINK("https://evil.example","Click")',
        '@import url("https://evil.example/a.css")',
    ]
    for bad in malicious_inputs:
        with pytest.raises(ProblemException) as exc_info:
            assert_safe_for_renderer(bad)
        assert exc_info.value.status_code == 422

    # Protected-trait guard in human feedback observations
    with pytest.raises(ProblemException) as trait_exc:
        validate_interview_observation_text(
            "Candidate discussed pregnancy plans and religion.",
            field_label="overall_observations",
        )
    assert trait_exc.value.status_code == 422


def test_docx_exporter_produces_editable_tables_unicode_and_not_assessed_semantics() -> None:
    payload = _sample_export_payload(include_feedback=True)
    docx_bytes = render_pack_docx(payload)
    assert len(docx_bytes) > 2000

    parsed = docx.Document(io.BytesIO(docx_bytes))
    all_paragraphs = "\n".join(p.text for p in parsed.paragraphs)
    all_table_cells = "\n".join(
        cell.text for tbl in parsed.tables for row in tbl.rows for cell in row.cells
    )
    combined = all_paragraphs + "\n" + all_table_cells

    assert len(parsed.paragraphs) >= 8
    assert len(parsed.tables) >= 3
    assert "Zofia Żółć-Nguyễn (東京)" in combined
    assert "München" in combined
    assert "Resume p.1 L12-L18" in combined
    assert "NOT_ASSESSED" in combined
    assert EXPORT_REVOCATION_DISCLOSURE in combined


def test_pdf_exporter_produces_readable_unicode_non_blank_pages_and_honors_timeout() -> None:
    payload = _sample_export_payload(include_feedback=True)
    pdf_bytes = render_pack_pdf(payload)
    assert pdf_bytes.startswith(b"%PDF-")

    # 1. Inspect extracted text and verify zero external URI actions via pypdf
    reader = pypdf.PdfReader(io.BytesIO(pdf_bytes))
    assert len(reader.pages) >= 1
    extracted_text = "\n".join(page.extract_text() or "" for page in reader.pages)

    assert "Zofia Żółć-Nguyễn" in extracted_text
    assert "Principal Distributed Systems Engineer" in extracted_text
    assert "Resume p.1 L12-L18" in extracted_text
    assert "NOT_ASSESSED" in extracted_text

    for page in reader.pages:
        annots = page.get("/Annots")
        assert not annots, "PDF export must not embed external clickable URI annotations"

    # 2. Visually rasterize first page with pypdfium2 to prove non-blank rendered layout
    pdf_doc = pdfium.PdfDocument(pdf_bytes)
    assert len(pdf_doc) >= 1
    pil_image = pdf_doc[0].render(scale=1.5).to_pil()
    assert pil_image.width > 500
    assert pil_image.height > 700
    extrema = pil_image.convert("L").getextrema()
    assert extrema is not None
    min_lum, max_lum = extrema
    assert min_lum < 80
    assert max_lum > 240

    # 3. Verify render timeout raises 503 LIMIT_EXCEEDED ProblemException cleanly
    timed_out_payload = _sample_export_payload(include_feedback=True, max_render_seconds=0.0)
    with pytest.raises(ProblemException) as exc_info:
        render_pack_pdf(timed_out_payload)
    assert exc_info.value.status_code == 503
    assert exc_info.value.code == "LIMIT_EXCEEDED"
