"""Organization, Member, Team, and Invitation Data Transfer Objects."""

from uuid import UUID

from pydantic import BaseModel, Field


class CreateOrganizationRequest(BaseModel):
    """Payload to create a new tenant organization."""

    name: str = Field(..., min_length=2, max_length=255)
    slug: str = Field(..., min_length=2, max_length=255)


class OrganizationResponse(BaseModel):
    """Organization summary with caller's membership role."""

    id: UUID
    name: str
    slug: str
    role: str
    status: str


class MemberResponse(BaseModel):
    """Organization member response."""

    membership_id: UUID
    user_id: UUID
    email: str
    role: str
    status: str
    created_at: str


class UpdateMemberRoleRequest(BaseModel):
    """Payload to update member role."""

    role: str = Field(
        ..., description="Target role: OWNER, ADMIN, RECRUITER, HIRING_MANAGER, INTERVIEWER"
    )


class TransferOwnershipRequest(BaseModel):
    """Payload to transfer organization ownership."""

    target_user_id: UUID


class CreateTeamRequest(BaseModel):
    """Payload to create a hiring team."""

    name: str = Field(..., min_length=2, max_length=255)
    description: str | None = None


class TeamResponse(BaseModel):
    """Team details response."""

    id: UUID
    tenant_id: UUID
    name: str
    description: str | None = None


class AddTeamMemberRequest(BaseModel):
    """Payload to assign a member to a team."""

    membership_id: UUID


class CreateInvitationRequest(BaseModel):
    """Payload to invite a new member."""

    email: str = Field(..., min_length=5, max_length=255)
    role: str = Field(
        ..., description="Assigned role: ADMIN, RECRUITER, HIRING_MANAGER, INTERVIEWER"
    )


class InvitationResponse(BaseModel):
    """Invitation details response."""

    id: UUID
    tenant_id: UUID
    email: str
    role: str
    status: str
    invitation_token: str | None = None


class AcceptInvitationRequest(BaseModel):
    """Payload to accept an invitation token."""

    token: str = Field(..., min_length=16)
