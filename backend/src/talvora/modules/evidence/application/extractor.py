"""Composite DocumentExtractor: Dispatches to the PDF or DOCX Adapter by Media Type.

This is the one object registered on `app.state.document_extractor` and consumed by
`ExtractionWorker`. It conforms to the same `DocumentExtractor` protocol as its two
concrete adapters, so a future third format only needs a new adapter plus one more
entry in `_adapters` -- callers never branch on media type themselves.
"""

from __future__ import annotations

from talvora.config import Settings
from talvora.modules.evidence.application.docx_extractor import DocxDocumentExtractor
from talvora.modules.evidence.application.ocr import TesseractOcrEngine
from talvora.modules.evidence.application.pdf_extractor import PdfDocumentExtractor
from talvora.modules.evidence.application.ports import (
    DocumentExtractor,
    ExtractionLimits,
    ExtractionOutcome,
    ExtractionOutcomeStatus,
    ExtractionProblemCode,
)


class CompositeDocumentExtractor:
    """Dispatches `extract` to whichever registered adapter `supports` the media type."""

    def __init__(self, adapters: list[DocumentExtractor]) -> None:
        self._adapters = adapters

    @classmethod
    def from_settings(cls, settings: Settings) -> CompositeDocumentExtractor:
        ocr_engine = TesseractOcrEngine(
            tesseract_cmd=settings.tesseract_cmd,
            language=settings.ocr_language,
            timeout_seconds=settings.ocr_page_timeout_seconds,
        )
        return cls(
            adapters=[
                PdfDocumentExtractor(ocr_engine=ocr_engine),
                DocxDocumentExtractor(),
            ]
        )

    def supports(self, media_type: str) -> bool:
        return any(adapter.supports(media_type) for adapter in self._adapters)

    def extract(self, data: bytes, media_type: str, limits: ExtractionLimits) -> ExtractionOutcome:
        for adapter in self._adapters:
            if adapter.supports(media_type):
                return adapter.extract(data, media_type, limits)
        return ExtractionOutcome(
            extractor_version="composite-v1",
            status=ExtractionOutcomeStatus.FAILED,
            problem_code=ExtractionProblemCode.DOCUMENT_UNREADABLE.value,
            error_detail=f"No extraction adapter supports media type '{media_type}'.",
            is_complete=False,
        )


def extractor_version_for_media_type(media_type: str) -> str:
    """The extractor pipeline version that will handle a given validated media type.

    Only PDF and DOCX ever reach a document version's `media_type` (P06 rejects any
    other container at upload time), so this never needs a third branch in this phase.
    """
    from talvora.modules.evidence.application.docx_extractor import (
        EXTRACTOR_VERSION as DOCX_EXTRACTOR_VERSION,
    )
    from talvora.modules.evidence.application.pdf_extractor import (
        EXTRACTOR_VERSION as PDF_EXTRACTOR_VERSION,
    )

    if media_type == "application/pdf":
        return PDF_EXTRACTOR_VERSION
    if media_type == "application/vnd.openxmlformats-officedocument.wordprocessingml.document":
        return DOCX_EXTRACTOR_VERSION
    raise ValueError(f"Unsupported media type for extraction: '{media_type}'.")


def default_extraction_limits(settings: Settings) -> ExtractionLimits:
    return ExtractionLimits(
        max_pages=settings.extraction_max_pdf_pages,
        max_characters=settings.extraction_max_characters,
        max_ocr_pages=settings.ocr_max_pages_per_document,
        ocr_dpi=settings.ocr_dpi,
        ocr_page_timeout_seconds=settings.ocr_page_timeout_seconds,
        ocr_min_confidence=settings.ocr_min_confidence_threshold,
        min_digital_text_chars_per_page=settings.extraction_min_digital_text_chars_per_page,
    )


__all__ = [
    "CompositeDocumentExtractor",
    "default_extraction_limits",
    "extractor_version_for_media_type",
]
