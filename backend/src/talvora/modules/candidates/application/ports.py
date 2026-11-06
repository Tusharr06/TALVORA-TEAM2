"""Repository Port Interfaces for Future Document and Profile References.

These protocols let the candidates/applications modules hold stable references to a
candidate's resume document and extracted profile without implementing (or fabricating)
either capability now. Document ingestion is P06/P07 and structured profile extraction
is P08; until those phases land, no implementation of these protocols exists and no
production code may claim a resume or evaluation result is present.
"""

from typing import Protocol, runtime_checkable
from uuid import UUID


@runtime_checkable
class CandidateDocumentReference(Protocol):
    """Read-only reference to a candidate's selected resume/document version.

    Implemented by the documents module (P06/P07). Until then, no adapter is registered
    and callers must treat an absent document as "unknown", never as "no resume".
    """

    async def get_active_document_version_id(
        self, tenant_id: UUID, candidate_id: UUID
    ) -> UUID | None:
        """Return the application-selected active resume document version, if any."""
        ...


@runtime_checkable
class CandidateProfileReference(Protocol):
    """Read-only reference to a candidate's reviewed structured profile.

    Implemented by the evidence module (P08). Absence of a profile is unknown, not a
    negative fact about the candidate, and this phase never fabricates one.
    """

    async def get_latest_reviewed_profile_revision_id(
        self, tenant_id: UUID, candidate_id: UUID
    ) -> UUID | None:
        """Return the latest human-reviewed profile revision id, if any exists."""
        ...
