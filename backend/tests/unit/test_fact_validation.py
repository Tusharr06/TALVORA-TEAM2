"""Unit tests for complete-phrase evidence validation."""

from talvora.modules.facts.domain.validation import is_evidence_supported


def test_evidence_support_matches_whole_tokens() -> None:
    assert is_evidence_supported("Java", "Experience with Java and Python.")
    assert not is_evidence_supported("Java", "Experience with JavaScript.")


def test_evidence_support_normalizes_case_and_whitespace() -> None:
    assert is_evidence_supported("POSTGRESQL", "Uses PostgreSQL\nfor production.")
