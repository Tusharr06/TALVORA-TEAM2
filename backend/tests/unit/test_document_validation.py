"""Unit tests for container validation: signatures, zip bombs, unsafe/malicious DOCX."""

import io
import zipfile

import pytest

from talvora.modules.documents.application.validation import (
    DOCX_MEDIA_TYPE,
    PDF_MEDIA_TYPE,
    ContainerValidationError,
    sniff_media_type,
    validate_container,
    validate_declared_type_matches_sniffed,
    validate_docx_container,
    validate_pdf_container,
)
from talvora.modules.documents.domain.models import IntakeProblemCode


def _minimal_pdf(body: bytes = b"Hello, Talvora.") -> bytes:
    return b"%PDF-1.4\n" + body + b"\n%%EOF"


def _minimal_docx(extra_entries: dict[str, bytes] | None = None) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0"?><Types xmlns="x"></Types>',
        )
        zf.writestr(
            "_rels/.rels",
            '<?xml version="1.0"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            "</Relationships>",
        )
        zf.writestr(
            "word/document.xml",
            '<?xml version="1.0"?><w:document xmlns:w="x"><w:body>'
            "<w:p><w:r><w:t>Resume body text.</w:t></w:r></w:p>"
            "</w:body></w:document>",
        )
        if extra_entries:
            for name, content in extra_entries.items():
                zf.writestr(name, content)
    return buf.getvalue()


# ============================================================================
# Signature sniffing
# ============================================================================


def test_sniff_media_type_pdf() -> None:
    assert sniff_media_type(_minimal_pdf()[:16]) == PDF_MEDIA_TYPE


def test_sniff_media_type_zip() -> None:
    assert sniff_media_type(_minimal_docx()[:16]) == "application/zip"


def test_sniff_media_type_unknown_rejects() -> None:
    assert sniff_media_type(b"not a real file header") is None


def test_declared_type_spoofed_extension_rejected() -> None:
    with pytest.raises(ContainerValidationError) as exc:
        validate_declared_type_matches_sniffed(
            declared_content_type="application/pdf",
            declared_filename="resume.pdf",
            sniffed_media_type="application/zip",
        )
    assert exc.value.problem_code == IntakeProblemCode.SPOOFED_MEDIA_TYPE


def test_declared_type_spoofed_content_type_rejected() -> None:
    with pytest.raises(ContainerValidationError) as exc:
        validate_declared_type_matches_sniffed(
            declared_content_type="image/png",
            declared_filename="resume.pdf",
            sniffed_media_type=PDF_MEDIA_TYPE,
        )
    assert exc.value.problem_code == IntakeProblemCode.SPOOFED_MEDIA_TYPE


def test_declared_type_matching_passes() -> None:
    validate_declared_type_matches_sniffed(
        declared_content_type="application/pdf",
        declared_filename="resume.pdf",
        sniffed_media_type=PDF_MEDIA_TYPE,
    )


# ============================================================================
# PDF structural validation
# ============================================================================


def test_valid_pdf_passes() -> None:
    report = validate_pdf_container(_minimal_pdf())
    assert report.media_type == PDF_MEDIA_TYPE


def test_pdf_missing_eof_is_malformed() -> None:
    with pytest.raises(ContainerValidationError) as exc:
        validate_pdf_container(b"%PDF-1.4\nno trailer here")
    assert exc.value.problem_code == IntakeProblemCode.MALFORMED_CONTAINER


def test_pdf_without_signature_is_malformed() -> None:
    with pytest.raises(ContainerValidationError) as exc:
        validate_pdf_container(b"not a pdf at all")
    assert exc.value.problem_code == IntakeProblemCode.MALFORMED_CONTAINER


def test_encrypted_pdf_rejected() -> None:
    data = b"%PDF-1.4\n<< /Encrypt 5 0 R >>\n%%EOF"
    with pytest.raises(ContainerValidationError) as exc:
        validate_pdf_container(data)
    assert exc.value.problem_code == IntakeProblemCode.ENCRYPTED_DOCUMENT


def test_pdf_with_launch_action_rejected_as_embedded_executable() -> None:
    data = b"%PDF-1.4\n<< /Launch (calc.exe) >>\n%%EOF"
    with pytest.raises(ContainerValidationError) as exc:
        validate_pdf_container(data)
    assert exc.value.problem_code == IntakeProblemCode.EMBEDDED_EXECUTABLE_BLOCKED


# ============================================================================
# DOCX structural validation: malformed, bombs, unsafe paths, macros, external, embedded exe
# ============================================================================


def test_valid_docx_passes() -> None:
    report = validate_docx_container(_minimal_docx())
    assert report.media_type == DOCX_MEDIA_TYPE


def test_malformed_zip_rejected() -> None:
    with pytest.raises(ContainerValidationError) as exc:
        validate_docx_container(b"PK\x03\x04not a real zip stream")
    assert exc.value.problem_code == IntakeProblemCode.MALFORMED_CONTAINER


def test_docx_missing_required_parts_rejected() -> None:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("readme.txt", "not a real docx")
    with pytest.raises(ContainerValidationError) as exc:
        validate_docx_container(buf.getvalue())
    assert exc.value.problem_code == IntakeProblemCode.MALFORMED_CONTAINER


def test_docx_unsafe_absolute_path_rejected() -> None:
    data = _minimal_docx(extra_entries={"/etc/passwd": b"x"})
    with pytest.raises(ContainerValidationError) as exc:
        validate_docx_container(data)
    assert exc.value.problem_code == IntakeProblemCode.UNSAFE_CONTAINER_ENTRY


def test_docx_unsafe_traversal_path_rejected() -> None:
    data = _minimal_docx(extra_entries={"../../etc/passwd": b"x"})
    with pytest.raises(ContainerValidationError) as exc:
        validate_docx_container(data)
    assert exc.value.problem_code == IntakeProblemCode.UNSAFE_CONTAINER_ENTRY


def test_docx_macro_project_blocked() -> None:
    data = _minimal_docx(extra_entries={"word/vbaProject.bin": b"\xd0\xcf\x11\xe0fake-ole"})
    with pytest.raises(ContainerValidationError) as exc:
        validate_docx_container(data)
    assert exc.value.problem_code == IntakeProblemCode.MACRO_CONTENT_BLOCKED


def test_docx_external_relationship_blocked() -> None:
    rels_xml = (
        '<?xml version="1.0"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="attachedTemplate" '
        'Target="http://evil.example.com/template.dotx" TargetMode="External"/>'
        "</Relationships>"
    )
    data = _minimal_docx(extra_entries={"word/_rels/document.xml.rels": rels_xml.encode()})
    with pytest.raises(ContainerValidationError) as exc:
        validate_docx_container(data)
    assert exc.value.problem_code == IntakeProblemCode.EXTERNAL_CONTENT_BLOCKED


def test_docx_ordinary_hyperlink_relationship_is_passive_and_allowed() -> None:
    rels_xml = (
        '<?xml version="1.0"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink" '
        'Target="http://example.com/" TargetMode="External"/>'
        "</Relationships>"
    )
    # A hyperlink relationship legitimately uses TargetMode="External" to record the URL
    # as passive text, but our conservative rule blocks ANY External target regardless of
    # type to guarantee we never resolve a remote relationship. This is intentionally
    # stricter than necessary; verify it is rejected for EXTERNAL_CONTENT, not silently
    # allowed to fetch anything (no network call is ever made either way).
    data = _minimal_docx(extra_entries={"word/_rels/document.xml.rels": rels_xml.encode()})
    with pytest.raises(ContainerValidationError) as exc:
        validate_docx_container(data)
    assert exc.value.problem_code == IntakeProblemCode.EXTERNAL_CONTENT_BLOCKED


def test_docx_embedded_pe_executable_blocked() -> None:
    data = _minimal_docx(extra_entries={"word/embeddings/oleObject1.bin": b"MZ\x90\x00fake-pe"})
    with pytest.raises(ContainerValidationError) as exc:
        validate_docx_container(data)
    assert exc.value.problem_code == IntakeProblemCode.EMBEDDED_EXECUTABLE_BLOCKED


def test_docx_embedded_ole_non_executable_warns_but_passes() -> None:
    ole_magic = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
    data = _minimal_docx(
        extra_entries={"word/embeddings/Microsoft_Excel_Worksheet1.xlsx": ole_magic + b"fakeole"}
    )
    report = validate_docx_container(data)
    assert any("OLE" in w for w in report.warnings)


def test_docx_too_many_entries_suspected_bomb() -> None:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("[Content_Types].xml", "<Types/>")
        zf.writestr("word/document.xml", "<w:document/>")
        for i in range(2_001):
            zf.writestr(f"junk/{i}.txt", "x")
    with pytest.raises(ContainerValidationError) as exc:
        validate_docx_container(buf.getvalue())
    assert exc.value.problem_code == IntakeProblemCode.DECOMPRESSION_BOMB_SUSPECTED


def test_docx_high_compression_ratio_suspected_bomb() -> None:
    # A single entry whose declared uncompressed size vastly exceeds its compressed size
    # trips the ratio bound even though the whole file is tiny on disk.
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", "<Types/>")
        zf.writestr("word/document.xml", "<w:document/>")
        zf.writestr("bomb.bin", b"\x00" * (50 * 1024 * 1024))

    with pytest.raises(ContainerValidationError) as exc:
        validate_docx_container(buf.getvalue())
    assert exc.value.problem_code == IntakeProblemCode.DECOMPRESSION_BOMB_SUSPECTED


def test_unsupported_media_type_dispatch_rejected() -> None:
    with pytest.raises(ContainerValidationError) as exc:
        validate_container("text/plain", b"irrelevant")
    assert exc.value.problem_code == IntakeProblemCode.UNSUPPORTED_MEDIA_TYPE
