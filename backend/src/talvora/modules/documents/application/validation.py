"""Real Byte-Level Container Validation for PDF and DOCX Resumes.

Deliberately does not use `Content-Type` or filename extension as evidence of the
actual media type: every input here is sniffed from the real bytes. This module stops
at *structural* validation (is this truly a PDF/DOCX container, and is it safe to hand
to a scanner and, eventually, to the P07 extractor) -- it never extracts resume text or
opens embedded content; that is explicitly out of scope for this phase.

Zip bomb/decompression-bomb bounds, unsafe path traversal, macro-enabled documents,
external/remote relationships, and embedded executable objects are all rejected here
deterministically, without ever resolving a URL or opening a nested document.
"""

from __future__ import annotations

import zipfile
from dataclasses import dataclass, field
from xml.etree.ElementTree import ParseError  # noqa: S405 (parsed only via defusedxml below)

import defusedxml.ElementTree as DefusedElementTree

from talvora.modules.documents.domain.models import IntakeProblemCode

PDF_MEDIA_TYPE = "application/pdf"
DOCX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

_PDF_MAGIC = b"%PDF-"
_ZIP_MAGIC_PREFIXES = (b"PK\x03\x04", b"PK\x05\x06")  # local file header / empty archive
_PE_MAGIC = b"MZ"
_OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"

# Conservative DOCX/zip-bomb bounds. The outer file is already capped (default 10 MiB)
# before this module ever runs, but a crafted small zip can still expand enormously.
MAX_ZIP_ENTRIES = 2_000
MAX_TOTAL_UNCOMPRESSED_BYTES = 200 * 1024 * 1024
MAX_SINGLE_ENTRY_UNCOMPRESSED_BYTES = 100 * 1024 * 1024
MAX_COMPRESSION_RATIO = 120  # uncompressed / compressed, per entry

_REQUIRED_DOCX_ENTRIES = {"[Content_Types].xml", "word/document.xml"}


class ContainerValidationError(Exception):
    """Raised with a stable, safe problem code when a container fails structural checks."""

    def __init__(self, problem_code: IntakeProblemCode, detail: str) -> None:
        super().__init__(detail)
        self.problem_code = problem_code
        self.detail = detail


@dataclass
class ValidationReport:
    """Result of successful structural validation."""

    media_type: str
    warnings: list[str] = field(default_factory=list)


def sniff_media_type(head: bytes) -> str | None:
    """Determine the actual media type from the leading bytes only. No trust in metadata."""
    if head.startswith(_PDF_MAGIC):
        return PDF_MEDIA_TYPE
    if any(head.startswith(p) for p in _ZIP_MAGIC_PREFIXES):
        return "application/zip"  # Further narrowed to DOCX by validate_docx_container.
    return None


def validate_declared_type_matches_sniffed(
    declared_content_type: str | None,
    declared_filename: str | None,
    sniffed_media_type: str,
) -> None:
    """Reject an item whose declared Content-Type/extension contradicts the real bytes.

    This never *trusts* the declared values -- it only uses them to detect an explicit
    spoofing attempt (e.g. a `.pdf`-named file whose bytes are actually a different
    container), which is reported distinctly from a generically unsupported format.
    """
    ext = None
    if declared_filename and "." in declared_filename:
        ext = declared_filename.rsplit(".", 1)[-1].lower()

    # Keyed by the *actual* value `sniff_media_type` returns -- a DOCX is sniffed as the
    # generic "application/zip" at this stage (it is only narrowed to the DOCX media
    # type after `validate_docx_container` confirms the required OOXML parts exist).
    allowed_extensions: dict[str, set[str]] = {
        PDF_MEDIA_TYPE: {"pdf"},
        "application/zip": {"docx"},
    }
    if (
        sniffed_media_type in allowed_extensions
        and ext is not None
        and ext not in allowed_extensions[sniffed_media_type]
    ):
        raise ContainerValidationError(
            IntakeProblemCode.SPOOFED_MEDIA_TYPE,
            f"Declared filename extension '.{ext}' does not match the sniffed "
            f"media type '{sniffed_media_type}' detected from the actual bytes.",
        )

    allowed_content_types: dict[str, set[str]] = {
        PDF_MEDIA_TYPE: {PDF_MEDIA_TYPE, "application/octet-stream"},
        "application/zip": {
            DOCX_MEDIA_TYPE,
            "application/zip",
            "application/octet-stream",
        },
    }
    if declared_content_type and sniffed_media_type in allowed_content_types:
        declared_norm = declared_content_type.split(";", 1)[0].strip().lower()
        if declared_norm not in allowed_content_types[sniffed_media_type]:
            raise ContainerValidationError(
                IntakeProblemCode.SPOOFED_MEDIA_TYPE,
                f"Declared Content-Type '{declared_content_type}' does not match the "
                f"sniffed media type '{sniffed_media_type}' detected from the actual bytes.",
            )


def validate_pdf_container(data: bytes) -> ValidationReport:
    """Conservative structural validation of a PDF without parsing/extracting content."""
    if not data.startswith(_PDF_MAGIC):
        raise ContainerValidationError(
            IntakeProblemCode.MALFORMED_CONTAINER, "File does not begin with a PDF signature."
        )

    trailer_region = data[-2048:]
    if b"%%EOF" not in trailer_region and b"%%EOF" not in data:
        raise ContainerValidationError(
            IntakeProblemCode.MALFORMED_CONTAINER,
            "PDF is missing a trailing %%EOF marker; the file may be truncated or corrupted.",
        )

    # Conservative encryption/DRM detection: an /Encrypt entry in any object/trailer
    # dictionary means the document requires a password or has restricted permissions.
    # Rather than guess which (possibly empty) password a reviewer should try, this
    # phase blocks the whole class and asks for an unencrypted replacement.
    if b"/Encrypt" in data:
        raise ContainerValidationError(
            IntakeProblemCode.ENCRYPTED_DOCUMENT,
            "PDF declares an /Encrypt dictionary. Encrypted PDFs are not accepted; "
            "upload an unencrypted replacement.",
        )

    warnings: list[str] = []
    if b"/JavaScript" in data or b"/JS " in data or b"/JS(" in data:
        warnings.append("PDF contains embedded JavaScript actions; content will not be executed.")
    if b"/Launch" in data or b"/EmbeddedFile" in data:
        raise ContainerValidationError(
            IntakeProblemCode.EMBEDDED_EXECUTABLE_BLOCKED,
            "PDF declares a /Launch action or /EmbeddedFile stream, which may reference "
            "an embedded executable payload.",
        )

    return ValidationReport(media_type=PDF_MEDIA_TYPE, warnings=warnings)


def _is_unsafe_zip_path(name: str) -> bool:
    if name.startswith("/") or name.startswith("\\"):
        return True
    if ".." in name.split("/"):
        return True
    return bool(name.startswith("~") or ":" in name)  # drive letters / home-relative escapes


def validate_docx_container(data: bytes) -> ValidationReport:
    """Structural validation of a DOCX (OOXML zip) with explicit decompression-bomb,
    unsafe-path, macro, external-relationship, and embedded-executable bounds.

    Never extracts resume text (that is P07's job) and never resolves any remote
    relationship or URL -- hyperlinks, if present, remain passive escaped text.
    """
    import io

    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise ContainerValidationError(
            IntakeProblemCode.MALFORMED_CONTAINER, f"Not a valid zip/DOCX container: {exc}"
        ) from exc

    try:
        infos = zf.infolist()
    except Exception as exc:  # pragma: no cover - zipfile raises BadZipFile above normally
        raise ContainerValidationError(
            IntakeProblemCode.MALFORMED_CONTAINER, f"Unable to read zip directory: {exc}"
        ) from exc

    if len(infos) > MAX_ZIP_ENTRIES:
        raise ContainerValidationError(
            IntakeProblemCode.DECOMPRESSION_BOMB_SUSPECTED,
            f"Container has {len(infos)} entries, exceeding the allowed maximum of "
            f"{MAX_ZIP_ENTRIES}.",
        )

    total_uncompressed = 0
    names: set[str] = set()
    for info in infos:
        name = info.filename
        names.add(name)

        if _is_unsafe_zip_path(name):
            raise ContainerValidationError(
                IntakeProblemCode.UNSAFE_CONTAINER_ENTRY,
                f"Container entry '{name}' uses an unsafe absolute or traversal path.",
            )

        if info.file_size > MAX_SINGLE_ENTRY_UNCOMPRESSED_BYTES:
            raise ContainerValidationError(
                IntakeProblemCode.DECOMPRESSION_BOMB_SUSPECTED,
                f"Container entry '{name}' declares {info.file_size} uncompressed bytes, "
                f"exceeding the per-entry bound of {MAX_SINGLE_ENTRY_UNCOMPRESSED_BYTES}.",
            )

        if info.compress_size > 0:
            ratio = info.file_size / max(info.compress_size, 1)
            if ratio > MAX_COMPRESSION_RATIO:
                raise ContainerValidationError(
                    IntakeProblemCode.DECOMPRESSION_BOMB_SUSPECTED,
                    f"Container entry '{name}' has a suspicious compression ratio of "
                    f"{ratio:.0f}x, exceeding the bound of {MAX_COMPRESSION_RATIO}x.",
                )

        total_uncompressed += info.file_size
        if total_uncompressed > MAX_TOTAL_UNCOMPRESSED_BYTES:
            raise ContainerValidationError(
                IntakeProblemCode.DECOMPRESSION_BOMB_SUSPECTED,
                "Container's total declared uncompressed size exceeds the allowed bound.",
            )

    missing = _REQUIRED_DOCX_ENTRIES - names
    if missing:
        raise ContainerValidationError(
            IntakeProblemCode.MALFORMED_CONTAINER,
            f"Container is missing required DOCX parts: {sorted(missing)}.",
        )

    # Macro-enabled documents are rejected outright regardless of declared content type.
    if any(n.lower() == "word/vbaproject.bin" for n in names):
        raise ContainerValidationError(
            IntakeProblemCode.MACRO_CONTENT_BLOCKED,
            "Container includes a VBA macro project (word/vbaProject.bin); "
            "macro-enabled documents are not accepted.",
        )

    warnings: list[str] = []

    # Inspect every *.rels part for external targets (remote templates, linked objects)
    # and OLE/package relationships pointing at embedded objects. We never fetch any
    # target; we only read the local XML describing the relationship.
    for name in names:
        if not name.endswith(".rels"):
            continue
        try:
            raw = zf.read(name)
        except Exception:  # pragma: no cover - defensive; already validated by infolist()
            continue
        try:
            root = DefusedElementTree.fromstring(raw)
        except ParseError as exc:
            raise ContainerValidationError(
                IntakeProblemCode.MALFORMED_CONTAINER,
                f"Relationship part '{name}' is not well-formed XML: {exc}",
            ) from exc

        for rel in root:
            target_mode = rel.attrib.get("TargetMode", "Internal")
            rel_type = rel.attrib.get("Type", "")
            if target_mode == "External":
                raise ContainerValidationError(
                    IntakeProblemCode.EXTERNAL_CONTENT_BLOCKED,
                    f"Relationship part '{name}' declares an External target. "
                    "External/remote relationships are not accepted.",
                )
            if "attachedTemplate" in rel_type or "oleObject" in rel_type.lower():
                raise ContainerValidationError(
                    IntakeProblemCode.EXTERNAL_CONTENT_BLOCKED,
                    f"Relationship part '{name}' declares a remote template or OLE "
                    "object relationship, which is not accepted.",
                )
            if "hyperlink" in rel_type.lower():
                # Ordinary hyperlinks remain passive escaped text; we deliberately never
                # resolve the target. No exception raised; no network access made.
                continue

    # Embedded objects: reject any entry under an embeddings directory that is itself an
    # executable (PE) or whose OLE compound-file payload is a package/executable rather
    # than e.g. an embedded spreadsheet chart.
    for info in infos:
        lower_name = info.filename.lower()
        if "embeddings/" not in lower_name and "/embeddings" not in lower_name:
            continue
        try:
            payload_head = zf.read(info.filename)[:8]
        except Exception:
            continue
        if payload_head.startswith(_PE_MAGIC):
            raise ContainerValidationError(
                IntakeProblemCode.EMBEDDED_EXECUTABLE_BLOCKED,
                f"Embedded object '{info.filename}' is a Windows executable (PE) payload.",
            )
        if payload_head.startswith(_OLE_MAGIC):
            warnings.append(
                f"Embedded OLE object '{info.filename}' detected; its contents are not "
                "inspected beyond container-level screening."
            )

    return ValidationReport(media_type=DOCX_MEDIA_TYPE, warnings=warnings)


def validate_container(sniffed_media_type: str, data: bytes) -> ValidationReport:
    """Dispatch to the format-specific structural validator."""
    if sniffed_media_type == PDF_MEDIA_TYPE:
        return validate_pdf_container(data)
    if sniffed_media_type == "application/zip":
        return validate_docx_container(data)
    raise ContainerValidationError(
        IntakeProblemCode.UNSUPPORTED_MEDIA_TYPE,
        f"Media type '{sniffed_media_type}' is not a supported resume container "
        "(only PDF and DOCX are accepted).",
    )
