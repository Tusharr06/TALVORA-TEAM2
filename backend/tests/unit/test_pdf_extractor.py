"""Unit tests for `PdfDocumentExtractor` (digital-text paths, bounds, and defensive
error handling). OCR is exercised through a lightweight stub engine here -- the real,
native-Tesseract OCR path is covered separately and exclusively by
`tests/integration/test_real_ocr_extraction.py`, per the P07 contract's requirement
to keep the real end-to-end OCR test apart from the fast mocked unit tests.
"""

from __future__ import annotations

from talvora.modules.evidence.application.ocr import OcrEngine, OcrPageResult, OcrUnavailableError
from talvora.modules.evidence.application.pdf_extractor import PdfDocumentExtractor
from talvora.modules.evidence.application.ports import (
    ExtractionLimits,
    ExtractionOutcomeStatus,
    SegmentExtractionMethod,
)
from tests.fixtures.synthetic.documents import (
    build_corrupted_pdf,
    build_digital_pdf,
    build_encrypted_pdf,
    build_many_page_digital_pdf,
    build_multicolumn_pdf_page,
    build_scanned_pdf_page,
    merge_pdfs,
)

_PDF_MEDIA_TYPE = "application/pdf"


class _NeverCalledOcrEngine:
    """Fails loudly if digital-text-only tests ever reach for OCR -- "avoid running
    OCR unnecessarily on good digital text" must be literally enforced, not assumed."""

    def ocr_png(self, png_bytes: bytes) -> OcrPageResult:
        raise AssertionError("OCR must not be invoked when digital text is sufficient")


class _StubOcrEngine:
    """A deterministic OCR stub for unit tests that need *a* result without invoking
    a real subprocess (the real binary is exercised in the separate real-OCR suite)."""

    def __init__(self, text: str = "stub ocr text", confidence: float | None = 95.0) -> None:
        self.text = text
        self.confidence = confidence
        self.calls = 0

    def ocr_png(self, png_bytes: bytes) -> OcrPageResult:
        self.calls += 1
        return OcrPageResult(text=self.text, mean_confidence=self.confidence)


class _FailingOcrEngine:
    def ocr_png(self, png_bytes: bytes) -> OcrPageResult:
        raise OcrUnavailableError("stub: tesseract unavailable")


def _extractor(ocr_engine: OcrEngine | None = None) -> PdfDocumentExtractor:
    return PdfDocumentExtractor(ocr_engine=ocr_engine or _NeverCalledOcrEngine())


def test_supports_pdf_media_type_only() -> None:
    extractor = _extractor()
    assert extractor.supports("application/pdf") is True
    assert (
        extractor.supports(
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        )
        is False
    )


def test_digital_multi_page_pdf_extracts_one_segment_per_page_with_one_based_pages() -> None:
    data = build_digital_pdf(
        [
            "Alpha page has plenty of real digital text content",
            "Beta page has plenty of real digital text content",
            "Gamma page has plenty of real digital text content",
        ]
    )
    outcome = _extractor().extract(data, _PDF_MEDIA_TYPE, ExtractionLimits())

    assert outcome.status == ExtractionOutcomeStatus.SUCCEEDED
    assert outcome.is_complete is True
    assert outcome.total_pages == 3
    assert outcome.processed_pages == 3
    assert len(outcome.segments) == 3
    assert [s.page_number for s in outcome.segments] == [1, 2, 3]
    assert all(
        s.extraction_method == SegmentExtractionMethod.DIGITAL_TEXT for s in outcome.segments
    )
    assert "Alpha page" in outcome.segments[0].text
    assert "Beta page" in outcome.segments[1].text
    assert "Gamma page" in outcome.segments[2].text
    assert outcome.ocr_pages_used == 0


def test_multicolumn_page_extracts_both_columns() -> None:
    data = build_multicolumn_pdf_page(
        left_lines=["Left Column Line 1", "Left Column Line 2"],
        right_lines=["Right Column Line 1", "Right Column Line 2"],
    )
    outcome = _extractor().extract(data, _PDF_MEDIA_TYPE, ExtractionLimits())
    assert outcome.status == ExtractionOutcomeStatus.SUCCEEDED
    combined = outcome.segments[0].text
    assert "Left Column Line 1" in combined
    assert "Right Column Line 1" in combined


def test_page_count_over_limit_is_bounded_and_marked_partial() -> None:
    data = build_many_page_digital_pdf(5)
    limits = ExtractionLimits(max_pages=3)
    outcome = _extractor().extract(data, _PDF_MEDIA_TYPE, limits)

    assert outcome.status == ExtractionOutcomeStatus.PARTIAL
    assert outcome.problem_code == "DOCUMENT_LIMIT_EXCEEDED"
    assert outcome.is_complete is False
    assert outcome.total_pages == 5
    assert outcome.processed_pages == 3
    assert "PAGE_LIMIT_EXCEEDED" in outcome.warnings
    assert len(outcome.segments) == 3


def test_character_cap_truncates_and_marks_partial() -> None:
    data = build_digital_pdf(["A" * 100, "B" * 100, "C" * 100])
    limits = ExtractionLimits(max_characters=150)
    outcome = _extractor().extract(data, _PDF_MEDIA_TYPE, limits)

    assert outcome.status == ExtractionOutcomeStatus.PARTIAL
    assert outcome.problem_code == "DOCUMENT_LIMIT_EXCEEDED"
    assert "CHARACTER_LIMIT_EXCEEDED" in outcome.warnings
    total_chars = sum(len(s.text) for s in outcome.segments)
    assert total_chars <= 150


def test_blank_page_with_ocr_unavailable_needs_review() -> None:
    """A page with no digital text at all falls back to OCR (it might be scanned);
    when OCR is also unavailable, the result is NEEDS_REVIEW, never a fabricated
    empty-but-successful page."""
    data = build_digital_pdf([""])
    outcome = PdfDocumentExtractor(ocr_engine=_FailingOcrEngine()).extract(
        data, _PDF_MEDIA_TYPE, ExtractionLimits()
    )
    assert outcome.status == ExtractionOutcomeStatus.NEEDS_REVIEW
    assert outcome.problem_code == "NEEDS_REVIEW"
    assert any(w.startswith("OCR_UNAVAILABLE") for w in outcome.segments[0].warnings)


def test_corrupted_pdf_fails_safely_with_document_unreadable() -> None:
    data = build_corrupted_pdf()
    outcome = _extractor().extract(data, _PDF_MEDIA_TYPE, ExtractionLimits())
    assert outcome.status == ExtractionOutcomeStatus.FAILED
    assert outcome.problem_code == "DOCUMENT_UNREADABLE"
    assert outcome.segments == []
    assert outcome.is_complete is False


def test_encrypted_pdf_fails_safely_with_document_unreadable() -> None:
    data = build_encrypted_pdf()
    outcome = _extractor().extract(data, _PDF_MEDIA_TYPE, ExtractionLimits())
    assert outcome.status == ExtractionOutcomeStatus.FAILED
    assert outcome.problem_code == "DOCUMENT_UNREADABLE"


def test_image_only_page_triggers_ocr_fallback() -> None:
    data = build_scanned_pdf_page("Stub OCR Target")
    stub = _StubOcrEngine(text="Stub OCR Target", confidence=97.0)
    outcome = PdfDocumentExtractor(ocr_engine=stub).extract(
        data, _PDF_MEDIA_TYPE, ExtractionLimits()
    )

    assert stub.calls == 1
    assert outcome.segments[0].extraction_method == SegmentExtractionMethod.OCR
    assert outcome.segments[0].text == "Stub OCR Target"
    assert outcome.segments[0].is_low_confidence is False
    assert outcome.ocr_pages_used == 1


def test_low_ocr_confidence_marks_segment_and_run_appropriately() -> None:
    data = build_scanned_pdf_page("Blurry")
    stub = _StubOcrEngine(text="Blurry", confidence=20.0)
    outcome = PdfDocumentExtractor(ocr_engine=stub).extract(
        data, _PDF_MEDIA_TYPE, ExtractionLimits()
    )

    assert outcome.segments[0].is_low_confidence is True
    assert "OCR_LOW_CONFIDENCE" in outcome.segments[0].warnings
    # A single page, all of it low confidence -> NEEDS_REVIEW, not a silent PARTIAL.
    assert outcome.status == ExtractionOutcomeStatus.NEEDS_REVIEW


def test_ocr_unavailable_never_fabricates_a_result() -> None:
    data = build_scanned_pdf_page("Unreachable OCR")
    outcome = PdfDocumentExtractor(ocr_engine=_FailingOcrEngine()).extract(
        data, _PDF_MEDIA_TYPE, ExtractionLimits()
    )
    segment = outcome.segments[0]
    assert segment.text == ""
    assert segment.is_low_confidence is True
    assert any(w.startswith("OCR_UNAVAILABLE") for w in segment.warnings)
    assert outcome.status == ExtractionOutcomeStatus.NEEDS_REVIEW


def test_ocr_page_budget_exhaustion_is_flagged_not_silently_skipped() -> None:
    scanned_pages = [build_scanned_pdf_page(f"Target {i}") for i in range(3)]
    data = merge_pdfs(scanned_pages)
    stub = _StubOcrEngine(text="ocr text", confidence=90.0)
    limits = ExtractionLimits(max_ocr_pages=1)
    outcome = PdfDocumentExtractor(ocr_engine=stub).extract(data, _PDF_MEDIA_TYPE, limits)

    assert stub.calls == 1
    assert outcome.ocr_pages_used == 1
    assert "OCR_PAGE_LIMIT_EXCEEDED" in outcome.warnings
    assert outcome.is_complete is False


def test_mixed_digital_and_scanned_pdf_uses_correct_method_per_page() -> None:
    digital = build_digital_pdf(["Digital page one has real extractable text"])
    scanned = build_scanned_pdf_page("Scanned page two")
    data = merge_pdfs([digital, scanned])
    stub = _StubOcrEngine(text="Scanned page two", confidence=95.0)
    outcome = PdfDocumentExtractor(ocr_engine=stub).extract(
        data, _PDF_MEDIA_TYPE, ExtractionLimits()
    )

    assert len(outcome.segments) == 2
    assert outcome.segments[0].extraction_method == SegmentExtractionMethod.DIGITAL_TEXT
    assert outcome.segments[1].extraction_method == SegmentExtractionMethod.OCR
    assert outcome.segments[0].page_number == 1
    assert outcome.segments[1].page_number == 2
