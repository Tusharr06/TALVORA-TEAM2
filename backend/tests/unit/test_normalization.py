"""Unit tests for the P07 versioned canonical text normalization pipeline."""

from talvora.modules.evidence.application.normalization import (
    NORMALIZATION_VERSION,
    canonical_text_sha256,
    normalize_text,
)


def test_normalization_version_is_pinned() -> None:
    assert NORMALIZATION_VERSION == "norm-v1"


def test_expands_typographic_ligatures() -> None:
    # U+FB01 LATIN SMALL LIGATURE FI -> "f" + "i"
    raw = "ﬁle"
    assert normalize_text(raw) == "file"


def test_expands_ligature_ffi() -> None:
    # U+FB03 LATIN SMALL LIGATURE FFI -> "f" + "f" + "i"
    raw = "staﬃng"
    assert normalize_text(raw) == "staffing"


def test_collapses_nbsp_and_other_unicode_spaces_to_ascii_space() -> None:
    raw = "John Smith"  # NBSP
    assert normalize_text(raw) == "John Smith"


def test_normalizes_crlf_and_lone_cr_to_lf() -> None:
    raw = "Line1\r\nLine2\rLine3\nLine4"
    assert normalize_text(raw) == "Line1\nLine2\nLine3\nLine4"


def test_collapses_horizontal_whitespace_runs_but_preserves_newlines() -> None:
    raw = "A    B\t\tC\n\nD"
    normalized = normalize_text(raw)
    assert normalized == "A B C\n\nD"


def test_strips_trailing_whitespace_per_line() -> None:
    raw = "First line   \nSecond line\t\n"
    normalized = normalize_text(raw)
    assert normalized == "First line\nSecond line"


def test_strips_leading_and_trailing_blank_text() -> None:
    raw = "\n\n  Body text  \n\n"
    assert normalize_text(raw) == "Body text"


def test_empty_and_none_like_input_produces_empty_string() -> None:
    assert normalize_text("") == ""


def test_normalization_is_idempotent() -> None:
    raw = "oﬁce  work\r\n\r\nSecond para"
    once = normalize_text(raw)
    twice = normalize_text(once)
    assert once == twice


def test_canonical_hash_is_deterministic_and_sensitive_to_content() -> None:
    a = canonical_text_sha256("Hello world")
    b = canonical_text_sha256("Hello world")
    c = canonical_text_sha256("Hello World")
    assert a == b
    assert a != c
    assert len(a) == 64
