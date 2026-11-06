"""Evidence module: document extraction, immutable source segments, and citations.

Bridges the clean, quarantine-scanned bytes produced by `documents` (P06) into
versioned, source-addressable text (`source_segment`) and bounded, hash-verified
citation anchors (`evidence_fragment`), via one or more immutable `extraction_run`
revisions per document version. No candidate decisions or fact extraction happen
here -- this module only produces reviewable, cited source text (P08 builds
candidate facts on top of it).
"""
