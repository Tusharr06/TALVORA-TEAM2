"""PDF DocumentExtractor Adapter: pypdf Digital Text + Bounded pypdfium2/Tesseract OCR.

Preserves one-based page locators (`ExtractedSegment.page_number`) exactly as the
playbook requires. Digital text is tried first for every page; OCR is only invoked
for a page whose digital text is empty/too sparse (likely a scanned or image-heavy
page), and only while the run's OCR-page budget remains -- "avoid running OCR
unnecessarily on good digital text" is enforced by this ordering, not by a separate
heuristic pass.

Resource bounds enforced here (all from `ExtractionLimits`, never unbounded):
page count, extracted-character count, OCR-page count, and a defensive pixel-count
cap on the OCR-rendered bitmap (guards against a crafted PDF declaring an enormous
page size). None of these limits is invented post-hoc -- hitting one always produces
an explicit coverage warning, never a silently truncated "complete" result.
"""

from __future__ import annotations

import io

import pypdf.errors
import pypdfium2 as pdfium
from pypdf import PdfReader

from talvora.modules.evidence.application.normalization import normalize_text
from talvora.modules.evidence.application.ocr import OcrEngine, OcrUnavailableError
from talvora.modules.evidence.application.ports import (
    ExtractedSegment,
    ExtractionLimits,
    ExtractionOutcome,
    ExtractionOutcomeStatus,
    ExtractionProblemCode,
    SegmentExtractionMethod,
    SourceLocatorType,
)

EXTRACTOR_VERSION = "pdf-pypdf5-pypdfium2-tesseract-v1"

_LIMIT_WARNING_CODES = frozenset(
    {"PAGE_LIMIT_EXCEEDED", "CHARACTER_LIMIT_EXCEEDED", "OCR_PAGE_LIMIT_EXCEEDED"}
)


class PdfDocumentExtractor:
    """Concrete `DocumentExtractor` adapter for `application/pdf`."""

    def __init__(self, ocr_engine: OcrEngine) -> None:
        self.ocr_engine = ocr_engine

    def supports(self, media_type: str) -> bool:
        return media_type == "application/pdf"

    def extract(self, data: bytes, media_type: str, limits: ExtractionLimits) -> ExtractionOutcome:
        try:
            reader = PdfReader(io.BytesIO(data))
            if reader.is_encrypted:
                return ExtractionOutcome(
                    extractor_version=EXTRACTOR_VERSION,
                    status=ExtractionOutcomeStatus.FAILED,
                    problem_code=ExtractionProblemCode.DOCUMENT_UNREADABLE.value,
                    error_detail="PDF is encrypted; cannot be read without a password.",
                    is_complete=False,
                )
            total_pages = len(reader.pages)
        except pypdf.errors.PyPdfError as exc:
            return ExtractionOutcome(
                extractor_version=EXTRACTOR_VERSION,
                status=ExtractionOutcomeStatus.FAILED,
                problem_code=ExtractionProblemCode.DOCUMENT_UNREADABLE.value,
                error_detail=f"Unable to parse PDF container: {exc}",
                is_complete=False,
            )
        except Exception as exc:  # defensive: never let a parser crash the worker
            return ExtractionOutcome(
                extractor_version=EXTRACTOR_VERSION,
                status=ExtractionOutcomeStatus.FAILED,
                problem_code=ExtractionProblemCode.DOCUMENT_UNREADABLE.value,
                error_detail=f"Unexpected PDF parsing failure: {exc}",
                is_complete=False,
            )

        if total_pages == 0:
            return ExtractionOutcome(
                extractor_version=EXTRACTOR_VERSION,
                status=ExtractionOutcomeStatus.NEEDS_REVIEW,
                problem_code=ExtractionProblemCode.NEEDS_REVIEW.value,
                error_detail="PDF contains zero pages.",
                is_complete=False,
                total_pages=0,
                processed_pages=0,
            )

        processed_pages = min(total_pages, limits.max_pages)
        doc_warnings: set[str] = set()
        if processed_pages < total_pages:
            doc_warnings.add("PAGE_LIMIT_EXCEEDED")

        fium_doc: pdfium.PdfDocument | None = None
        try:
            segments: list[ExtractedSegment] = []
            ocr_pages_used = 0
            running_char_total = 0
            character_cap_hit = False

            for page_index in range(processed_pages):
                page = reader.pages[page_index]
                try:
                    digital_text = page.extract_text() or ""
                except Exception:  # defensive: a malformed single page must not abort the run
                    digital_text = ""
                normalized_digital = normalize_text(digital_text)

                if len(normalized_digital) >= limits.min_digital_text_chars_per_page:
                    segment = ExtractedSegment(
                        locator_type=SourceLocatorType.PDF_PAGE,
                        extraction_method=SegmentExtractionMethod.DIGITAL_TEXT,
                        text=normalized_digital,
                        page_number=page_index + 1,
                    )
                else:
                    if fium_doc is None:
                        fium_doc = pdfium.PdfDocument(io.BytesIO(data))
                    segment, used_ocr = self._extract_via_ocr(
                        fium_doc=fium_doc,
                        page_index=page_index,
                        fallback_text=normalized_digital,
                        limits=limits,
                        ocr_budget_remaining=limits.max_ocr_pages - ocr_pages_used,
                    )
                    if used_ocr:
                        ocr_pages_used += 1

                remaining_budget = limits.max_characters - running_char_total
                if remaining_budget <= 0:
                    character_cap_hit = True
                    break
                if len(segment.text) > remaining_budget:
                    segment = ExtractedSegment(
                        locator_type=segment.locator_type,
                        extraction_method=segment.extraction_method,
                        text=segment.text[:remaining_budget],
                        page_number=segment.page_number,
                        docx_path=segment.docx_path,
                        is_low_confidence=segment.is_low_confidence,
                        ocr_mean_confidence=segment.ocr_mean_confidence,
                        warnings=segment.warnings,
                    )
                    character_cap_hit = True

                running_char_total += len(segment.text)
                segments.append(segment)
                if character_cap_hit:
                    break

            if character_cap_hit:
                doc_warnings.add("CHARACTER_LIMIT_EXCEEDED")
            if any("OCR_PAGE_LIMIT_EXCEEDED" in s.warnings for s in segments):
                doc_warnings.add("OCR_PAGE_LIMIT_EXCEEDED")
        finally:
            if fium_doc is not None:
                fium_doc.close()

        status, problem_code = _assess_quality(segments, doc_warnings)
        is_complete = not (doc_warnings & _LIMIT_WARNING_CODES)

        return ExtractionOutcome(
            extractor_version=EXTRACTOR_VERSION,
            status=status,
            segments=segments,
            is_complete=is_complete,
            warnings=tuple(sorted(doc_warnings)),
            problem_code=problem_code,
            total_pages=total_pages,
            processed_pages=len(segments),
            ocr_pages_used=sum(
                1 for s in segments if s.extraction_method == SegmentExtractionMethod.OCR
            ),
        )

    def _extract_via_ocr(
        self,
        fium_doc: pdfium.PdfDocument,
        page_index: int,
        fallback_text: str,
        limits: ExtractionLimits,
        ocr_budget_remaining: int,
    ) -> tuple[ExtractedSegment, bool]:
        """Returns (segment, used_ocr_budget)."""
        page_number = page_index + 1
        if ocr_budget_remaining <= 0:
            return (
                ExtractedSegment(
                    locator_type=SourceLocatorType.PDF_PAGE,
                    extraction_method=SegmentExtractionMethod.DIGITAL_TEXT,
                    text=fallback_text,
                    page_number=page_number,
                    is_low_confidence=True,
                    warnings=("OCR_PAGE_LIMIT_EXCEEDED",),
                ),
                False,
            )

        try:
            fium_page = fium_doc[page_index]
        except Exception as exc:
            return (
                ExtractedSegment(
                    locator_type=SourceLocatorType.PDF_PAGE,
                    extraction_method=SegmentExtractionMethod.DIGITAL_TEXT,
                    text=fallback_text,
                    page_number=page_number,
                    is_low_confidence=True,
                    warnings=(f"OCR_RENDER_FAILED:{exc}"[:200],),
                ),
                False,
            )

        # pypdfium2's `render()` already bakes the page's own stored `/Rotate`
        # attribute into the output bitmap by default (`rotation=0` below means "no
        # *additional* rotation on top of that", not "ignore the page's rotation").
        # Passing `fium_page.get_rotation()` here would double-apply it (e.g. a
        # 90-degree page would render at 180 degrees), which is exactly what produced
        # unreadable, upside-down OCR input during development -- confirmed directly
        # against the real Tesseract binary before fixing this.
        width_pt, height_pt = fium_page.get_size()
        scale = limits.ocr_dpi / 72.0
        estimated_pixels = (width_pt * scale) * (height_pt * scale)
        if estimated_pixels > limits.max_ocr_render_pixels:
            return (
                ExtractedSegment(
                    locator_type=SourceLocatorType.PDF_PAGE,
                    extraction_method=SegmentExtractionMethod.DIGITAL_TEXT,
                    text=fallback_text,
                    page_number=page_number,
                    is_low_confidence=True,
                    warnings=("PAGE_TOO_LARGE_FOR_OCR",),
                ),
                False,
            )

        try:
            bitmap = fium_page.render(scale=scale)
            pil_image = bitmap.to_pil()
            buf = io.BytesIO()
            pil_image.save(buf, format="PNG")
            png_bytes = buf.getvalue()
        except Exception as exc:
            return (
                ExtractedSegment(
                    locator_type=SourceLocatorType.PDF_PAGE,
                    extraction_method=SegmentExtractionMethod.DIGITAL_TEXT,
                    text=fallback_text,
                    page_number=page_number,
                    is_low_confidence=True,
                    warnings=(f"OCR_RENDER_FAILED:{exc}"[:200],),
                ),
                False,
            )

        try:
            ocr_result = self.ocr_engine.ocr_png(png_bytes)
        except OcrUnavailableError as exc:
            return (
                ExtractedSegment(
                    locator_type=SourceLocatorType.PDF_PAGE,
                    extraction_method=SegmentExtractionMethod.DIGITAL_TEXT,
                    text=fallback_text,
                    page_number=page_number,
                    is_low_confidence=True,
                    warnings=(f"OCR_UNAVAILABLE:{exc}"[:200],),
                ),
                True,
            )

        normalized_ocr_text = normalize_text(ocr_result.text)
        is_low_confidence = (
            ocr_result.mean_confidence is None
            or ocr_result.mean_confidence < limits.ocr_min_confidence
        )
        warnings: tuple[str, ...] = ("OCR_LOW_CONFIDENCE",) if is_low_confidence else ()
        return (
            ExtractedSegment(
                locator_type=SourceLocatorType.PDF_PAGE,
                extraction_method=SegmentExtractionMethod.OCR,
                text=normalized_ocr_text,
                page_number=page_number,
                is_low_confidence=is_low_confidence,
                ocr_mean_confidence=ocr_result.mean_confidence,
                warnings=warnings,
            ),
            True,
        )


def _assess_quality(
    segments: list[ExtractedSegment], doc_warnings: set[str]
) -> tuple[ExtractionOutcomeStatus, str | None]:
    total_chars = sum(len(s.text) for s in segments)
    if total_chars == 0:
        return ExtractionOutcomeStatus.NEEDS_REVIEW, ExtractionProblemCode.NEEDS_REVIEW.value

    low_confidence_segments = [s for s in segments if s.is_low_confidence]
    if segments and len(low_confidence_segments) == len(segments):
        return ExtractionOutcomeStatus.NEEDS_REVIEW, ExtractionProblemCode.NEEDS_REVIEW.value

    all_warnings = set(doc_warnings) | {w.split(":", 1)[0] for s in segments for w in s.warnings}
    if all_warnings:
        problem_code = (
            ExtractionProblemCode.DOCUMENT_LIMIT_EXCEEDED.value
            if all_warnings & _LIMIT_WARNING_CODES
            else None
        )
        return ExtractionOutcomeStatus.PARTIAL, problem_code

    return ExtractionOutcomeStatus.SUCCEEDED, None


__all__ = ["EXTRACTOR_VERSION", "PdfDocumentExtractor"]
