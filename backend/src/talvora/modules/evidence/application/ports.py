"""DocumentExtractor Port: Extraction Outcomes, Segments, and Typed Limits.

Mirrors the shape of `talvora.modules.documents.application.ports` (the P06
`ObjectStoragePort`/`VirusScannerPort` boundary): a `Protocol` the application/worker
code depends on, with concrete adapters (pypdf/python-docx/pypdfium2+Tesseract)
living in sibling modules. `DocumentExtractor` makes no candidate decisions and
returns no scores -- only source-addressable text, quality flags, and coverage
warnings, per the playbook's `DocumentExtractor` provider-port definition.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol, runtime_checkable


class SegmentExtractionMethod(StrEnum):
    """How one source segment's text was produced."""

    DIGITAL_TEXT = "DIGITAL_TEXT"
    OCR = "OCR"
    DOCX_STRUCTURED = "DOCX_STRUCTURED"


class SourceLocatorType(StrEnum):
    """The shape of a source segment's locator within its document version."""

    PDF_PAGE = "PDF_PAGE"
    DOCX_BODY_PARAGRAPH = "DOCX_BODY_PARAGRAPH"
    DOCX_TABLE_CELL = "DOCX_TABLE_CELL"
    DOCX_HEADER = "DOCX_HEADER"
    DOCX_FOOTER = "DOCX_FOOTER"


class ExtractionOutcomeStatus(StrEnum):
    """Overall quality/completeness verdict for one extraction attempt."""

    SUCCEEDED = "SUCCEEDED"
    PARTIAL = "PARTIAL"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    FAILED = "FAILED"


class ExtractionProblemCode(StrEnum):
    """Safe, stable problem codes surfaced for a non-SUCCEEDED extraction outcome."""

    DOCUMENT_UNREADABLE = "DOCUMENT_UNREADABLE"
    DOCUMENT_LIMIT_EXCEEDED = "DOCUMENT_LIMIT_EXCEEDED"
    NEEDS_REVIEW = "NEEDS_REVIEW"


@dataclass(frozen=True)
class ExtractionLimits:
    """Bounded worker budget for one extraction attempt. Never unbounded."""

    max_pages: int = 30
    max_characters: int = 200_000
    max_ocr_pages: int = 10
    ocr_dpi: int = 200
    ocr_page_timeout_seconds: float = 20.0
    ocr_min_confidence: float = 60.0
    min_digital_text_chars_per_page: int = 20
    max_ocr_render_pixels: int = 40_000_000


@dataclass(frozen=True)
class ExtractedSegment:
    """One already-normalized, immutable unit of source text."""

    locator_type: SourceLocatorType
    extraction_method: SegmentExtractionMethod
    text: str
    page_number: int | None = None
    docx_path: str | None = None
    is_low_confidence: bool = False
    ocr_mean_confidence: float | None = None
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class ExtractionOutcome:
    """The full, typed result of one `DocumentExtractor.extract` call."""

    extractor_version: str
    status: ExtractionOutcomeStatus
    segments: list[ExtractedSegment] = field(default_factory=list)
    is_complete: bool = True
    warnings: tuple[str, ...] = ()
    problem_code: str | None = None
    error_detail: str | None = None
    total_pages: int | None = None
    processed_pages: int | None = None
    ocr_pages_used: int = 0


@runtime_checkable
class DocumentExtractor(Protocol):
    """Extracts source segments and quality warnings. No candidate decisions."""

    def supports(self, media_type: str) -> bool:
        """Whether this adapter can handle the given validated media type."""
        ...

    def extract(self, data: bytes, media_type: str, limits: ExtractionLimits) -> ExtractionOutcome:
        """Extract bounded, source-addressable text. Never raises for a bad document;
        a corrupted/encrypted/oversized input is reported as a FAILED/NEEDS_REVIEW/
        PARTIAL `ExtractionOutcome`, never a fabricated success."""
        ...


__all__ = [
    "DocumentExtractor",
    "ExtractedSegment",
    "ExtractionLimits",
    "ExtractionOutcome",
    "ExtractionOutcomeStatus",
    "ExtractionProblemCode",
    "SegmentExtractionMethod",
    "SourceLocatorType",
]
