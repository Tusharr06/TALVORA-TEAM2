"""Actor and Principal Context Representation.

Encapsulates authenticated identity, active tenant membership, and granted capabilities.
"""

from dataclasses import dataclass, field
from typing import Literal
from uuid import UUID

BaseRole = Literal["OWNER", "ADMIN", "RECRUITER", "HIRING_MANAGER", "INTERVIEWER"]


@dataclass(frozen=True)
class Principal:
    """The authenticated physical or system identity."""

    user_id: UUID
    email: str
    issuer: str
    subject: str


@dataclass(frozen=True)
class MembershipContext:
    """Active membership context within a specific organization."""

    membership_id: UUID
    organization_id: UUID
    role: BaseRole
    capabilities: frozenset[str] = field(default_factory=frozenset)


@dataclass(frozen=True)
class CandidateCapabilityContext:
    """Candidate portal link context (isolated from internal staff sessions)."""

    interview_request_id: UUID
    candidate_id: UUID
    recipient_email: str


@dataclass(frozen=True)
class ActorContext:
    """Unified actor context for authorization evaluation."""

    principal: Principal | None = None
    membership: MembershipContext | None = None
    candidate_link: CandidateCapabilityContext | None = None

    @property
    def is_authenticated(self) -> bool:
        return self.principal is not None

    @property
    def is_staff(self) -> bool:
        return self.principal is not None and self.membership is not None

    @property
    def is_candidate(self) -> bool:
        return self.candidate_link is not None

    @property
    def user_id(self) -> UUID:
        if self.principal is None:
            raise ValueError("ActorContext has no authenticated principal.")
        return self.principal.user_id

    @property
    def tenant_id(self) -> UUID | None:
        return self.membership.organization_id if self.membership else None

    def has_capability(self, capability: str) -> bool:
        if not self.membership:
            return False
        if self.membership.role == "OWNER":
            return True
        return capability in self.membership.capabilities
