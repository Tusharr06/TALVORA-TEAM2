"""Unit tests for `DocxDocumentExtractor`: document-order paragraph/table traversal,
nested tables, headers/footers, unsupported-feature detection, and bounds."""

from __future__ import annotations

from talvora.modules.evidence.application.docx_extractor import DocxDocumentExtractor
from talvora.modules.evidence.application.ports import (
    ExtractionLimits,
    ExtractionOutcome,
    ExtractionOutcomeStatus,
    SegmentExtractionMethod,
    SourceLocatorType,
)
from tests.fixtures.synthetic.documents import (
    build_corrupted_docx,
    build_docx,
    build_docx_with_textbox,
    build_docx_with_tracked_changes,
)

_DOCX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def _extract(data: bytes, limits: ExtractionLimits | None = None) -> ExtractionOutcome:
    return DocxDocumentExtractor().extract(data, _DOCX_MEDIA_TYPE, limits or ExtractionLimits())


def test_supports_docx_media_type_only() -> None:
    extractor = DocxDocumentExtractor()
    assert extractor.supports(_DOCX_MEDIA_TYPE) is True
    assert extractor.supports("application/pdf") is False


def test_body_paragraphs_in_document_order_with_no_fabricated_page_number() -> None:
    data = build_docx(paragraphs=["First paragraph", "Second paragraph", "Third paragraph"])
    outcome = _extract(data)

    assert outcome.status == ExtractionOutcomeStatus.SUCCEEDED
    assert outcome.is_complete is True
    assert outcome.total_pages is None
    assert len(outcome.segments) == 3
    assert [s.text for s in outcome.segments] == [
        "First paragraph",
        "Second paragraph",
        "Third paragraph",
    ]
    assert [s.docx_path for s in outcome.segments] == [
        "body/paragraph[0]",
        "body/paragraph[1]",
        "body/paragraph[2]",
    ]
    assert all(s.page_number is None for s in outcome.segments)
    assert all(s.locator_type == SourceLocatorType.DOCX_BODY_PARAGRAPH for s in outcome.segments)
    assert all(
        s.extraction_method == SegmentExtractionMethod.DOCX_STRUCTURED for s in outcome.segments
    )


def test_table_cells_get_deterministic_paths() -> None:
    data = build_docx(
        paragraphs=["Intro"],
        table_rows=[["r0c0", "r0c1"], ["r1c0", "r1c1"]],
    )
    outcome = _extract(data)
    paths = {s.docx_path: s.text for s in outcome.segments}

    assert paths["body/paragraph[0]"] == "Intro"
    assert paths["body/table[0]/row[0]/cell[0]/paragraph[0]"] == "r0c0"
    assert paths["body/table[0]/row[0]/cell[1]/paragraph[0]"] == "r0c1"
    assert paths["body/table[0]/row[1]/cell[0]/paragraph[0]"] == "r1c0"
    assert paths["body/table[0]/row[1]/cell[1]/paragraph[0]"] == "r1c1"


def test_nested_table_inside_a_cell_gets_a_nested_path() -> None:
    data = build_docx(
        table_rows=[["outer00", "outer01"], ["outer10", "outer11"]],
        nested_table_rows=[["inner00"]],
    )
    outcome = _extract(data)
    paths = {s.docx_path: s.text for s in outcome.segments}

    assert paths["body/table[0]/row[0]/cell[0]/table[0]/row[0]/cell[0]/paragraph[0]"] == "inner00"
    assert paths["body/table[0]/row[0]/cell[1]/paragraph[0]"] == "outer01"


def test_header_and_footer_are_extracted_with_no_fabricated_page_number() -> None:
    data = build_docx(paragraphs=["Body"], header_text="Jane Candidate", footer_text="Confidential")
    outcome = _extract(data)
    paths = {s.docx_path: (s.text, s.locator_type) for s in outcome.segments}

    assert paths["header[0]/paragraph[0]"] == ("Jane Candidate", SourceLocatorType.DOCX_HEADER)
    assert paths["footer[0]/paragraph[0]"] == ("Confidential", SourceLocatorType.DOCX_FOOTER)
    assert all(s.page_number is None for s in outcome.segments)


def test_empty_paragraphs_are_skipped_not_emitted_as_segments() -> None:
    data = build_docx(paragraphs=["Real text", "", "   ", "More text"])
    outcome = _extract(data)
    assert [s.text for s in outcome.segments] == ["Real text", "More text"]


def test_textbox_content_is_flagged_as_unsupported_and_not_complete() -> None:
    data = build_docx_with_textbox()
    outcome = _extract(data)

    assert "UNSUPPORTED_TEXTBOX_CONTENT" in outcome.warnings
    assert outcome.is_complete is False
    assert outcome.status == ExtractionOutcomeStatus.PARTIAL
    # The visible body paragraph is still extracted -- coverage warning, not silence.
    assert any(s.text == "Visible body text" for s in outcome.segments)
    # The textbox's own hidden content is never visited.
    assert all("Hidden textbox text" not in s.text for s in outcome.segments)


def test_tracked_changes_are_flagged_as_unsupported_and_not_complete() -> None:
    data = build_docx_with_tracked_changes()
    outcome = _extract(data)

    assert "UNSUPPORTED_TRACKED_CHANGES" in outcome.warnings
    assert outcome.is_complete is False
    assert outcome.status == ExtractionOutcomeStatus.PARTIAL


def test_plain_docx_with_no_unsupported_features_has_no_warnings() -> None:
    data = build_docx(paragraphs=["Just a normal paragraph."])
    outcome = _extract(data)
    assert outcome.warnings == ()
    assert outcome.is_complete is True
    assert outcome.status == ExtractionOutcomeStatus.SUCCEEDED


def test_corrupted_docx_fails_safely_with_document_unreadable() -> None:
    data = build_corrupted_docx()
    outcome = _extract(data)
    assert outcome.status == ExtractionOutcomeStatus.FAILED
    assert outcome.problem_code == "DOCUMENT_UNREADABLE"
    assert outcome.segments == []


def test_completely_empty_docx_needs_review() -> None:
    data = build_docx(paragraphs=[])
    outcome = _extract(data)
    assert outcome.status == ExtractionOutcomeStatus.NEEDS_REVIEW
    assert outcome.problem_code == "NEEDS_REVIEW"


def test_character_cap_truncates_and_marks_partial() -> None:
    data = build_docx(paragraphs=["A" * 100, "B" * 100, "C" * 100])
    limits = ExtractionLimits(max_characters=150)
    outcome = _extract(data, limits)

    assert outcome.status == ExtractionOutcomeStatus.PARTIAL
    assert outcome.problem_code == "DOCUMENT_LIMIT_EXCEEDED"
    assert "CHARACTER_LIMIT_EXCEEDED" in outcome.warnings
    total_chars = sum(len(s.text) for s in outcome.segments)
    assert total_chars <= 150


def test_document_lacking_any_date_metadata_never_fabricates_one() -> None:
    """A DOCX with no date fields at all must not cause the extractor to invent a
    date anywhere in its output -- this phase stores no document-level date, so the
    absence of one in core-properties must simply produce no date-shaped output."""
    data = build_docx(paragraphs=["No dates mentioned anywhere in this resume body."])
    outcome = _extract(data)
    assert outcome.status == ExtractionOutcomeStatus.SUCCEEDED
    combined_text = " ".join(s.text for s in outcome.segments)
    assert "1970" not in combined_text
    assert "created_at" not in combined_text.lower()
