"""Strict text checks for evidence-derived candidate fact values."""

import re


def is_evidence_supported(value: str, quote: str) -> bool:
    """Match a complete normalized phrase, not a substring of a longer token."""
    normalized_value = " ".join(value.casefold().split())
    normalized_quote = " ".join(quote.casefold().split())
    if not normalized_value:
        return False
    pattern = rf"(?<!\w){re.escape(normalized_value)}(?!\w)"
    return re.search(pattern, normalized_quote) is not None
