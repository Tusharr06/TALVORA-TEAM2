"""Organization, Member, Team, and Invitation REST Endpoints."""

from typing import cast
from uuid import UUID

from fastapi import APIRouter, Depends, Path, Request, Response
from sqlalchemy.orm import Session

from talvora.config import Settings, get_settings
from talvora.modules.identity.api.dependencies import (
    enforce_csrf_and_origin,
    get_active_tenant_id,
    get_current_actor,
    get_db_session,
    get_session_service,
)
from talvora.modules.identity.application.authorization import AuthorizationService
from talvora.modules.identity.application.session_service import SessionService
from talvora.modules.identity.domain.roles import Capability
from talvora.modules.organizations.api.dto import (
    AcceptInvitationRequest,
    AddTeamMemberRequest,
    CreateInvitationRequest,
    CreateOrganizationRequest,
    CreateTeamRequest,
    InvitationResponse,
    MemberResponse,
    OrganizationResponse,
    TeamResponse,
    TransferOwnershipRequest,
    UpdateMemberRoleRequest,
)
from talvora.modules.organizations.application.service import OrganizationService
from talvora.shared.application.actor import ActorContext
from talvora.shared.infrastructure.mail import InMemorySystemMailProvider, SystemMailProvider
from talvora.shared.infrastructure.uow import UnitOfWork

router = APIRouter(tags=["Organizations & Teams"])


def get_mail_provider(request: Request) -> SystemMailProvider:
    """Dependency returning configured system mail provider."""
    custom_mail = getattr(request.app.state, "mail_provider", None)
    if custom_mail is not None:
        return cast(SystemMailProvider, custom_mail)
    return InMemorySystemMailProvider()


# --- Organizations ---


@router.get("/organizations", response_model=list[OrganizationResponse])
def list_organizations(
    actor: ActorContext = Depends(get_current_actor),
    db: Session = Depends(get_db_session),
) -> list[OrganizationResponse]:
    """List organizations where the caller holds active membership."""
    service = OrganizationService()
    orgs = service.list_user_organizations(db, actor.user_id)
    return [
        OrganizationResponse(
            id=UUID(str(org["id"])),
            name=str(org["name"]),
            slug=str(org["slug"]),
            role=str(org["role"]),
            status=str(org["status"]),
        )
        for org in orgs
    ]


@router.post(
    "/organizations",
    response_model=OrganizationResponse,
    dependencies=[Depends(enforce_csrf_and_origin)],
)
def create_organization(
    payload: CreateOrganizationRequest,
    actor: ActorContext = Depends(get_current_actor),
    db: Session = Depends(get_db_session),
) -> OrganizationResponse:
    """Create a new organization and atomically assign caller as OWNER."""
    service = OrganizationService()
    with UnitOfWork(session=db, tenant_id=None, subject_id=actor.user_id) as uow:
        org = service.create_organization(
            uow=uow,
            creator_user_id=actor.user_id,
            name=payload.name,
            slug=payload.slug,
        )

    return OrganizationResponse(
        id=org.id,
        name=org.name,
        slug=org.slug,
        role="OWNER",
        status="ACTIVE",
    )


@router.post(
    "/organizations/{id}/select",
    dependencies=[Depends(enforce_csrf_and_origin)],
)
def select_organization(
    request: Request,
    response: Response,
    organization_id: UUID = Path(..., alias="id"),
    actor: ActorContext = Depends(get_current_actor),
    settings: Settings = Depends(get_settings),
    db: Session = Depends(get_db_session),
    session_service: SessionService = Depends(get_session_service),
) -> dict[str, str]:
    """Switch active tenant context for the current session (Session Rotation)."""
    auth_session = getattr(request.state, "auth_session", None)
    if auth_session is None:
        AuthorizationService.enforce_capability(
            actor, Capability.VIEW_MEMBERS, target_tenant_id=organization_id
        )

    # Verify user has active membership in target organization
    org_service = OrganizationService()
    orgs = org_service.list_user_organizations(db, actor.user_id)
    target_found = any(UUID(str(o["id"])) == organization_id for o in orgs)
    if not target_found:
        AuthorizationService.enforce_capability(
            actor, Capability.VIEW_MEMBERS, target_tenant_id=organization_id
        )

    # Rotate session with new tenant_id
    assert auth_session is not None
    _, new_token = session_service.rotate_session(
        db_session=db,
        old_session_id=auth_session.id,
        new_tenant_id=organization_id,
    )
    db.commit()

    # Set fresh cookie
    cookie_name = settings.session_cookie_name
    response.set_cookie(
        key=cookie_name,
        value=new_token,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        path="/",
        max_age=settings.session_absolute_timeout_hours * 3600,
    )
    return {
        "message": "Active organization switched successfully",
        "organization_id": str(organization_id),
    }


# --- Member Administration ---


@router.get("/organizations/{id}/members", response_model=list[MemberResponse])
def list_members(
    organization_id: UUID = Path(..., alias="id"),
    actor: ActorContext = Depends(get_current_actor),
    db: Session = Depends(get_db_session),
) -> list[MemberResponse]:
    """List members of an organization (requires VIEW_MEMBERS capability)."""
    AuthorizationService.enforce_capability(
        actor=actor,
        capability=Capability.VIEW_MEMBERS,
        target_tenant_id=organization_id,
    )
    service = OrganizationService()
    members = service.list_members(db, organization_id)
    return [
        MemberResponse(
            membership_id=UUID(str(m["membership_id"])),
            user_id=UUID(str(m["user_id"])),
            email=str(m["email"]),
            role=str(m["role"]),
            status=str(m["status"]),
            created_at=str(m["created_at"]),
        )
        for m in members
    ]


@router.patch(
    "/organizations/{id}/members/{user_id}",
    response_model=MemberResponse,
    dependencies=[Depends(enforce_csrf_and_origin)],
)
def update_member_role(
    user_id: UUID,
    payload: UpdateMemberRoleRequest,
    organization_id: UUID = Path(..., alias="id"),
    actor: ActorContext = Depends(get_current_actor),
    db: Session = Depends(get_db_session),
) -> MemberResponse:
    """Update role of an organization member (enforces Last Owner Protection)."""
    AuthorizationService.enforce_capability(
        actor=actor,
        capability=Capability.MANAGE_MEMBERS,
        target_tenant_id=organization_id,
    )
    service = OrganizationService()
    with UnitOfWork(session=db, tenant_id=organization_id, subject_id=actor.user_id) as uow:
        updated = service.update_member_role(
            uow=uow,
            tenant_id=organization_id,
            target_user_id=user_id,
            new_role=payload.role,
        )

    # Fetch email for response
    members = service.list_members(db, organization_id)
    email = next((str(m["email"]) for m in members if UUID(str(m["user_id"])) == user_id), "")

    return MemberResponse(
        membership_id=updated.id,
        user_id=updated.user_id,
        email=email,
        role=updated.role,
        status=updated.status,
        created_at=updated.created_at.isoformat(),
    )


@router.delete(
    "/organizations/{id}/members/{user_id}",
    dependencies=[Depends(enforce_csrf_and_origin)],
)
def remove_member(
    user_id: UUID,
    organization_id: UUID = Path(..., alias="id"),
    actor: ActorContext = Depends(get_current_actor),
    db: Session = Depends(get_db_session),
    session_service: SessionService = Depends(get_session_service),
) -> dict[str, str]:
    """Offboard member: revokes membership and all active sessions (Last Owner Protected)."""
    AuthorizationService.enforce_capability(
        actor=actor,
        capability=Capability.MANAGE_MEMBERS,
        target_tenant_id=organization_id,
    )
    service = OrganizationService()
    with UnitOfWork(session=db, tenant_id=organization_id, subject_id=actor.user_id) as uow:
        service.remove_member(
            uow=uow,
            tenant_id=organization_id,
            target_user_id=user_id,
        )
        # Terminate active sessions for the offboarded member
        session_service.revoke_all_user_sessions(db, user_id)

    return {"message": "Member removed and sessions invalidated successfully"}


@router.post(
    "/organizations/{id}/transfer-ownership",
    dependencies=[Depends(enforce_csrf_and_origin)],
)
def transfer_ownership(
    payload: TransferOwnershipRequest,
    organization_id: UUID = Path(..., alias="id"),
    actor: ActorContext = Depends(get_current_actor),
    db: Session = Depends(get_db_session),
) -> dict[str, str]:
    """Transfer organization ownership atomically."""
    AuthorizationService.enforce_capability(
        actor=actor,
        capability=Capability.TRANSFER_OWNERSHIP,
        target_tenant_id=organization_id,
    )
    service = OrganizationService()
    with UnitOfWork(session=db, tenant_id=organization_id, subject_id=actor.user_id) as uow:
        service.transfer_ownership(
            uow=uow,
            tenant_id=organization_id,
            current_owner_id=actor.user_id,
            target_user_id=payload.target_user_id,
        )

    return {"message": "Ownership transferred successfully"}


# --- Teams ---


@router.get("/teams", response_model=list[TeamResponse])
def list_teams(
    tenant_id: UUID = Depends(get_active_tenant_id),
    actor: ActorContext = Depends(get_current_actor),
    db: Session = Depends(get_db_session),
) -> list[TeamResponse]:
    """List teams in the active organization."""
    AuthorizationService.enforce_capability(
        actor=actor,
        capability=Capability.VIEW_TEAMS,
        target_tenant_id=tenant_id,
    )
    service = OrganizationService()
    teams = service.list_teams(db, tenant_id)
    return [
        TeamResponse(
            id=t.id,
            tenant_id=t.tenant_id,
            name=t.name,
            description=t.description,
        )
        for t in teams
    ]


@router.post(
    "/teams",
    response_model=TeamResponse,
    dependencies=[Depends(enforce_csrf_and_origin)],
)
def create_team(
    payload: CreateTeamRequest,
    tenant_id: UUID = Depends(get_active_tenant_id),
    actor: ActorContext = Depends(get_current_actor),
    db: Session = Depends(get_db_session),
) -> TeamResponse:
    """Create a new hiring team in the active organization."""
    AuthorizationService.enforce_capability(
        actor=actor,
        capability=Capability.MANAGE_TEAMS,
        target_tenant_id=tenant_id,
    )
    service = OrganizationService()
    with UnitOfWork(session=db, tenant_id=tenant_id, subject_id=actor.user_id) as uow:
        team = service.create_team(
            uow=uow,
            tenant_id=tenant_id,
            name=payload.name,
            description=payload.description,
        )

    return TeamResponse(
        id=team.id,
        tenant_id=team.tenant_id,
        name=team.name,
        description=team.description,
    )


@router.post(
    "/teams/{team_id}/members",
    dependencies=[Depends(enforce_csrf_and_origin)],
)
def add_team_member(
    team_id: UUID,
    payload: AddTeamMemberRequest,
    tenant_id: UUID = Depends(get_active_tenant_id),
    actor: ActorContext = Depends(get_current_actor),
    db: Session = Depends(get_db_session),
) -> dict[str, str]:
    """Assign an active member to a team."""
    AuthorizationService.enforce_capability(
        actor=actor,
        capability=Capability.MANAGE_TEAMS,
        target_tenant_id=tenant_id,
    )
    service = OrganizationService()
    with UnitOfWork(session=db, tenant_id=tenant_id, subject_id=actor.user_id) as uow:
        tm = service.add_team_member(
            uow=uow,
            tenant_id=tenant_id,
            team_id=team_id,
            membership_id=payload.membership_id,
        )

    return {"message": "Team member assigned successfully", "team_member_id": str(tm.id)}


@router.delete(
    "/teams/{team_id}/members/{membership_id}",
    dependencies=[Depends(enforce_csrf_and_origin)],
)
def remove_team_member(
    team_id: UUID,
    membership_id: UUID,
    tenant_id: UUID = Depends(get_active_tenant_id),
    actor: ActorContext = Depends(get_current_actor),
    db: Session = Depends(get_db_session),
) -> dict[str, str]:
    """Remove a member from a team."""
    AuthorizationService.enforce_capability(
        actor=actor,
        capability=Capability.MANAGE_TEAMS,
        target_tenant_id=tenant_id,
    )
    service = OrganizationService()
    with UnitOfWork(session=db, tenant_id=tenant_id, subject_id=actor.user_id) as uow:
        service.remove_team_member(
            uow=uow,
            tenant_id=tenant_id,
            team_id=team_id,
            membership_id=membership_id,
        )

    return {"message": "Team member removed successfully"}


# --- Membership Invitations ---


@router.post(
    "/membership-invitations",
    response_model=InvitationResponse,
    dependencies=[Depends(enforce_csrf_and_origin)],
)
def create_invitation(
    payload: CreateInvitationRequest,
    tenant_id: UUID = Depends(get_active_tenant_id),
    actor: ActorContext = Depends(get_current_actor),
    db: Session = Depends(get_db_session),
    mail: SystemMailProvider = Depends(get_mail_provider),
) -> InvitationResponse:
    """Create a single-use hashed membership invitation and enqueue system mail."""
    AuthorizationService.enforce_capability(
        actor=actor,
        capability=Capability.INVITE_MEMBERS,
        target_tenant_id=tenant_id,
    )
    service = OrganizationService()
    with UnitOfWork(session=db, tenant_id=tenant_id, subject_id=actor.user_id) as uow:
        inv, raw_token = service.create_invitation(
            uow=uow,
            tenant_id=tenant_id,
            invited_by_user_id=actor.user_id,
            email=payload.email,
            role=payload.role,
            mail_provider=mail,
        )

    return InvitationResponse(
        id=inv.id,
        tenant_id=inv.tenant_id,
        email=inv.email,
        role=inv.role,
        status=inv.status,
        invitation_token=raw_token,
    )


@router.post(
    "/membership-invitations/{id}/accept",
    dependencies=[Depends(enforce_csrf_and_origin)],
)
def accept_invitation_by_id(
    payload: AcceptInvitationRequest,
    invitation_id: UUID = Path(..., alias="id"),
    actor: ActorContext = Depends(get_current_actor),
    db: Session = Depends(get_db_session),
) -> dict[str, str]:
    """Redeem an invitation token by invitation ID."""
    service = OrganizationService()
    with UnitOfWork(session=db, tenant_id=None, subject_id=actor.user_id) as uow:
        membership = service.accept_invitation(
            uow=uow,
            token=payload.token,
            accepting_user_id=actor.user_id,
        )

    return {
        "message": "Invitation accepted successfully",
        "membership_id": str(membership.id),
        "organization_id": str(membership.tenant_id),
        "role": membership.role,
    }


@router.post(
    "/membership-invitations/accept",
    dependencies=[Depends(enforce_csrf_and_origin)],
)
def accept_invitation(
    payload: AcceptInvitationRequest,
    actor: ActorContext = Depends(get_current_actor),
    db: Session = Depends(get_db_session),
) -> dict[str, str]:
    """Redeem an invitation token directly."""
    service = OrganizationService()
    with UnitOfWork(session=db, tenant_id=None, subject_id=actor.user_id) as uow:
        membership = service.accept_invitation(
            uow=uow,
            token=payload.token,
            accepting_user_id=actor.user_id,
        )

    return {
        "message": "Invitation accepted successfully",
        "membership_id": str(membership.id),
        "organization_id": str(membership.tenant_id),
        "role": membership.role,
    }
