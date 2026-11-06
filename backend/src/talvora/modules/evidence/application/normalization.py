"""Canonical Text Normalization (Versioned).

Every `SourceSegment.text` stored by this phase is the output of exactly one
deterministic normalization pipeline, identified by `NORMALIZATION_VERSION`. Offsets
persisted on `EvidenceFragment` are Unicode *code-point* offsets into that exact
stored string -- never byte offsets, and never offsets into the raw, un-normalized
extractor output. Changing any step below requires bumping `NORMALIZATION_VERSION`
and letting a new extraction revision pick it up; existing `SourceSegment`/
`EvidenceFragment` rows are never mutated in place to match a new pipeline (P07
contract: "do not mutate text beneath existing citations").

Pipeline (`normalize_text`), in order:

1. Unicode normalization form **NFKC**. This expands typographic/compatibility
   ligatures (e.g. U+FB01 "ﬁ" -> "f" + "i") and compatibility whitespace (e.g.
   U+00A0 NBSP -> U+0020 space) into their canonical equivalents, so a later
   substring/offset lookup never has to special-case ligatures.
2. Line-ending normalization: CRLF and lone CR are rewritten to LF ("\n").
3. Horizontal whitespace collapsing: any run of one or more non-newline whitespace
   code points (regex `[^\\S\\n]+`, which also matches Unicode space separators
   NFKC did not already fold to ASCII space) is collapsed to a single ASCII space.
4. Trailing-horizontal-whitespace stripping on every line.
5. Leading/trailing blank-line stripping of the whole segment (`str.strip()`).

Internal blank lines (paragraph breaks) are deliberately preserved -- step 3 only
collapses *horizontal* whitespace, never collapses multiple newlines into one.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata

NORMALIZATION_VERSION = "norm-v1"

_CRLF_OR_CR_RE = re.compile(r"\r\n|\r")
_HORIZONTAL_WHITESPACE_RUN_RE = re.compile(r"[^\S\n]+")
_TRAILING_LINE_WHITESPACE_RE = re.compile(r"[ \t]+\n")


def normalize_text(raw: str) -> str:
    """Apply the exact, versioned normalization pipeline documented above."""
    if not raw:
        return ""

    text = unicodedata.normalize("NFKC", raw)
    text = _CRLF_OR_CR_RE.sub("\n", text)
    text = _HORIZONTAL_WHITESPACE_RUN_RE.sub(" ", text)
    text = _TRAILING_LINE_WHITESPACE_RE.sub("\n", text)
    return text.strip()


def canonical_text_sha256(text: str) -> str:
    """Hash of the exact normalized text, used to detect any post-hoc mutation."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


__all__ = ["NORMALIZATION_VERSION", "canonical_text_sha256", "normalize_text"]
