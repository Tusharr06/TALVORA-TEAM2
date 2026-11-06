"""DOCX DocumentExtractor Adapter: Deterministic Document-Order Paragraph/Table Walk.

Traverses body paragraphs and tables (including nested tables inside table cells) in
true document order using the standard python-docx "iter block items" recipe, plus
default section headers/footers that are not merely inherited (`is_linked_to_previous`)
from a prior section. Each non-empty paragraph becomes exactly one `ExtractedSegment`
with a deterministic `docx_path` (e.g. `body/paragraph[3]`,
`body/table[0]/row[1]/cell[0]/paragraph[0]`, `body/table[0]/row[1]/cell[0]/table[0]/
row[0]/cell[0]/paragraph[0]` for a nested table).

A DOCX file never has a stable page layout without a separately versioned rendering
(the playbook is explicit about this) -- `page_number` is always `None` here; no
code path in this module invents one.

Unsupported-content detection is a raw substring scan over the package's own XML
parts (never a full document-order feature), consistent with the P06 container
validator's own style of narrow, explicit detection:

- a `txbxContent` element anywhere indicates a text box, whose content this walker
  does not visit (a coverage warning is raised instead of silently claiming
  completeness);
- an `w:ins`/`w:del` element anywhere indicates tracked changes/revisions, whose
  precise accepted/rejected text this walker does not resolve (it reads whatever
  python-docx's plain `.text` property returns, which is the raw mixed content).
"""

from __future__ import annotations

import io
import re
import zipfile
from collections.abc import Iterator
from typing import Any

import docx
from docx.document import Document as DocxDocument
from docx.opc.exceptions import PackageNotFoundError
from docx.oxml.table import CT_Tbl
from docx.oxml.text.paragraph import CT_P
from docx.table import Table, _Cell
from docx.text.paragraph import Paragraph

from talvora.modules.evidence.application.normalization import normalize_text
from talvora.modules.evidence.application.ports import (
    ExtractedSegment,
    ExtractionLimits,
    ExtractionOutcome,
    ExtractionOutcomeStatus,
    ExtractionProblemCode,
    SegmentExtractionMethod,
    SourceLocatorType,
)

EXTRACTOR_VERSION = "docx-python-docx-v1"

_TEXTBOX_RE = re.compile(rb":txbxContent\b")
_TRACKED_CHANGE_RE = re.compile(rb"<[a-zA-Z]+:(ins|del)[ />]")


def _iter_block_items(container_element: Any) -> Iterator[CT_P | CT_Tbl]:
    """Yield raw `<w:p>`/`<w:tbl>` elements of `container_element` in document order."""
    for child in container_element.iterchildren():
        if isinstance(child, (CT_P, CT_Tbl)):
            yield child


class DocxDocumentExtractor:
    """Concrete `DocumentExtractor` adapter for DOCX (OOXML WordprocessingML)."""

    def supports(self, media_type: str) -> bool:
        return (
            media_type == "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        )

    def extract(self, data: bytes, media_type: str, limits: ExtractionLimits) -> ExtractionOutcome:
        try:
            document = docx.Document(io.BytesIO(data))
        except PackageNotFoundError as exc:
            return ExtractionOutcome(
                extractor_version=EXTRACTOR_VERSION,
                status=ExtractionOutcomeStatus.FAILED,
                problem_code=ExtractionProblemCode.DOCUMENT_UNREADABLE.value,
                error_detail=f"Unable to open DOCX package: {exc}",
                is_complete=False,
            )
        except Exception as exc:  # defensive: never let a parser crash the worker
            return ExtractionOutcome(
                extractor_version=EXTRACTOR_VERSION,
                status=ExtractionOutcomeStatus.FAILED,
                problem_code=ExtractionProblemCode.DOCUMENT_UNREADABLE.value,
                error_detail=f"Unexpected DOCX parsing failure: {exc}",
                is_complete=False,
            )

        doc_warnings: set[str] = set()
        for code, pattern in (
            ("UNSUPPORTED_TEXTBOX_CONTENT", _TEXTBOX_RE),
            ("UNSUPPORTED_TRACKED_CHANGES", _TRACKED_CHANGE_RE),
        ):
            if _scan_package_xml_for_pattern(data, pattern):
                doc_warnings.add(code)
        if _scan_header_footer_xml_for_table(data):
            doc_warnings.add("UNSUPPORTED_HEADER_FOOTER_TABLE")

        segments: list[ExtractedSegment] = []
        running_char_total = 0
        character_cap_hit = False

        def emit(locator_type: SourceLocatorType, docx_path: str, raw_text: str) -> bool:
            """Returns False (and sets the cap flag) once the character budget is spent."""
            nonlocal running_char_total, character_cap_hit
            normalized = normalize_text(raw_text)
            if not normalized:
                return True
            remaining = limits.max_characters - running_char_total
            if remaining <= 0:
                character_cap_hit = True
                return False
            if len(normalized) > remaining:
                normalized = normalized[:remaining]
                character_cap_hit = True
            running_char_total += len(normalized)
            segments.append(
                ExtractedSegment(
                    locator_type=locator_type,
                    extraction_method=SegmentExtractionMethod.DOCX_STRUCTURED,
                    text=normalized,
                    docx_path=docx_path,
                )
            )
            return not character_cap_hit

        try:
            self._walk_body(document, emit)
            if not character_cap_hit:
                self._walk_headers_and_footers(document, emit)
        except Exception as exc:  # defensive: a single malformed element must not abort the run
            doc_warnings.add("STRUCTURE_WALK_INCOMPLETE")
            if not segments:
                return ExtractionOutcome(
                    extractor_version=EXTRACTOR_VERSION,
                    status=ExtractionOutcomeStatus.FAILED,
                    problem_code=ExtractionProblemCode.DOCUMENT_UNREADABLE.value,
                    error_detail=f"Unexpected DOCX structure walk failure: {exc}",
                    is_complete=False,
                )

        if character_cap_hit:
            doc_warnings.add("CHARACTER_LIMIT_EXCEEDED")

        total_chars = sum(len(s.text) for s in segments)
        if total_chars == 0:
            status = ExtractionOutcomeStatus.NEEDS_REVIEW
            problem_code: str | None = ExtractionProblemCode.NEEDS_REVIEW.value
        elif doc_warnings:
            limit_related = {"CHARACTER_LIMIT_EXCEEDED"}
            problem_code = (
                ExtractionProblemCode.DOCUMENT_LIMIT_EXCEEDED.value
                if doc_warnings & limit_related
                else None
            )
            status = ExtractionOutcomeStatus.PARTIAL
        else:
            status = ExtractionOutcomeStatus.SUCCEEDED
            problem_code = None

        is_complete = not doc_warnings

        return ExtractionOutcome(
            extractor_version=EXTRACTOR_VERSION,
            status=status,
            segments=segments,
            is_complete=is_complete,
            warnings=tuple(sorted(doc_warnings)),
            problem_code=problem_code,
            total_pages=None,
            processed_pages=None,
            ocr_pages_used=0,
        )

    # ------------------------------------------------------------------
    # Document-order traversal
    # ------------------------------------------------------------------

    def _walk_body(self, document: DocxDocument, emit: Any) -> None:
        body_paragraph_index = 0
        top_table_index = 0
        for element in _iter_block_items(document.element.body):
            if isinstance(element, CT_P):
                paragraph = Paragraph(element, document)
                if emit(
                    SourceLocatorType.DOCX_BODY_PARAGRAPH,
                    f"body/paragraph[{body_paragraph_index}]",
                    paragraph.text,
                ):
                    body_paragraph_index += 1
                else:
                    return
            elif isinstance(element, CT_Tbl):
                table = Table(element, document)
                if not self._walk_table(table, f"body/table[{top_table_index}]", emit):
                    return
                top_table_index += 1

    def _walk_table(self, table: Table, path_prefix: str, emit: Any) -> bool:
        for row_index, row in enumerate(table.rows):
            for cell_index, cell in enumerate(row.cells):
                cell_path = f"{path_prefix}/row[{row_index}]/cell[{cell_index}]"
                if not self._walk_cell(cell, cell_path, emit):
                    return False
        return True

    def _walk_cell(self, cell: _Cell, path_prefix: str, emit: Any) -> bool:
        paragraph_index = 0
        nested_table_index = 0
        for element in _iter_block_items(cell._tc):
            if isinstance(element, CT_P):
                paragraph = Paragraph(element, cell)
                if emit(
                    SourceLocatorType.DOCX_TABLE_CELL,
                    f"{path_prefix}/paragraph[{paragraph_index}]",
                    paragraph.text,
                ):
                    paragraph_index += 1
                else:
                    return False
            elif isinstance(element, CT_Tbl):
                nested_table = Table(element, cell)
                nested_path = f"{path_prefix}/table[{nested_table_index}]"
                if not self._walk_table(nested_table, nested_path, emit):
                    return False
                nested_table_index += 1
        return True

    def _walk_headers_and_footers(self, document: DocxDocument, emit: Any) -> None:
        for section_index, section in enumerate(document.sections):
            for kind, part, locator in (
                ("header", section.header, SourceLocatorType.DOCX_HEADER),
                ("footer", section.footer, SourceLocatorType.DOCX_FOOTER),
            ):
                if part.is_linked_to_previous:
                    continue  # inherited from a prior section: no new content here.
                paragraph_index = 0
                for element in _iter_block_items(part._element):
                    if isinstance(element, CT_P):
                        paragraph = Paragraph(element, part)
                        if emit(
                            locator,
                            f"{kind}[{section_index}]/paragraph[{paragraph_index}]",
                            paragraph.text,
                        ):
                            paragraph_index += 1
                        else:
                            return
                    # A table inside a header/footer is a rare, unsupported layout for
                    # this phase (`_scan_header_footer_xml_for_table` below surfaces it
                    # as an explicit coverage warning rather than silently dropping it).


_HEADER_FOOTER_PART_RE = re.compile(r"^word/(header|footer)\d*\.xml$")
_TABLE_TAG_RE = re.compile(rb":tbl[ >]")


def _scan_header_footer_xml_for_table(data: bytes) -> bool:
    """True if any header/footer OOXML part contains a table -- a layout this
    walker's header/footer traversal does not visit (paragraphs only)."""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            for name in zf.namelist():
                if not _HEADER_FOOTER_PART_RE.match(name):
                    continue
                if _TABLE_TAG_RE.search(zf.read(name)):
                    return True
    except zipfile.BadZipFile:
        return False
    return False


def _scan_package_xml_for_pattern(data: bytes, pattern: re.Pattern[bytes]) -> bool:
    """Bytes-level scan of every `.xml` part for `pattern`. No XML parsing is performed
    here (and so no XXE surface is introduced) -- this is a narrow presence check only,
    exactly like P06's own `vbaProject.bin` detection."""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            for name in zf.namelist():
                if not name.endswith(".xml"):
                    continue
                content = zf.read(name)
                if pattern.search(content):
                    return True
    except zipfile.BadZipFile:
        return False
    return False


__all__ = ["EXTRACTOR_VERSION", "DocxDocumentExtractor"]
