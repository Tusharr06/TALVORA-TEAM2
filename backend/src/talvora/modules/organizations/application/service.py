"""Organization, Membership, Team, and Invitation Management Service."""

import re
from datetime import UTC, timedelta
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.orm import Session

from talvora.modules.identity.domain.models import User
from talvora.modules.identity.domain.roles import Role
from talvora.modules.organizations.domain.models import (
    Membership,
    MembershipInvitation,
    Organization,
    Team,
    TeamMember,
)
from talvora.shared.domain.clock import Clock, SystemClock
from talvora.shared.infrastructure.mail import SystemMailProvider
from talvora.shared.infrastructure.security import generate_token_256, hash_token
from talvora.shared.infrastructure.uow import UnitOfWork
from talvora.shared.transport.problems import ProblemException


class OrganizationService:
    """Encapsulates organization lifecycle, last-owner invariants, and team operations."""

    def __init__(self, clock: Clock | None = None) -> None:
        self.clock = clock or SystemClock()

    def list_user_organizations(self, session: Session, user_id: UUID) -> list[dict[str, object]]:
        """List all organizations where the user holds an active membership."""
        stmt = (
            sa.select(Organization, Membership)
            .join(Membership, Membership.tenant_id == Organization.id)
            .where(Membership.user_id == user_id, Membership.status == "ACTIVE")
            .order_by(Organization.name.asc())
        )
        rows = session.execute(stmt).all()
        return [
            {
                "id": str(org.id),
                "name": org.name,
                "slug": org.slug,
                "role": mem.role,
                "status": mem.status,
            }
            for org, mem in rows
        ]

    def create_organization(
        self,
        uow: UnitOfWork,
        creator_user_id: UUID,
        name: str,
        slug: str,
    ) -> Organization:
        """Atomically create an organization and assign creator as initial OWNER."""
        clean_slug = re.sub(r"[^a-z0-9-]", "", slug.lower().strip().replace(" ", "-"))
        if not clean_slug:
            raise ProblemException(
                status_code=400,
                title="Invalid Slug",
                code="INVALID_ORGANIZATION_SLUG",
                detail="Organization slug cannot be empty and must contain alphanumeric characters.",
            )

        # Check slug uniqueness
        existing = uow.session.scalar(
            sa.select(Organization).where(Organization.slug == clean_slug)
        )
        if existing:
            raise ProblemException(
                status_code=409,
                title="Slug Conflict",
                code="ORGANIZATION_SLUG_CONFLICT",
                detail=f"Organization with slug '{clean_slug}' already exists.",
            )

        org = Organization(name=name.strip(), slug=clean_slug)
        uow.session.add(org)
        uow.session.flush()

        # Set tenant_id for uow and create owner membership
        uow.tenant_id = org.id
        membership = Membership(
            tenant_id=org.id,
            user_id=creator_user_id,
            role=Role.OWNER.value,
            status="ACTIVE",
        )
        uow.session.add(membership)
        uow.session.flush()

        uow.record_audit_event(
            action="ORGANIZATION_CREATED",
            resource_type="organization",
            resource_id=org.id,
            payload={"name": org.name, "slug": org.slug, "owner_id": str(creator_user_id)},
        )
        return org

    def list_members(self, session: Session, tenant_id: UUID) -> list[dict[str, object]]:
        """List active and invited members of the organization."""
        stmt = (
            sa.select(Membership, User)
            .join(User, User.id == Membership.user_id)
            .where(Membership.tenant_id == tenant_id, Membership.status != "REVOKED")
            .order_by(Membership.created_at.asc())
        )
        rows = session.execute(stmt).all()
        return [
            {
                "membership_id": str(mem.id),
                "user_id": str(usr.id),
                "email": usr.email,
                "role": mem.role,
                "status": mem.status,
                "created_at": mem.created_at.isoformat(),
            }
            for mem, usr in rows
        ]

    def update_member_role(
        self,
        uow: UnitOfWork,
        tenant_id: UUID,
        target_user_id: UUID,
        new_role: str,
    ) -> Membership:
        """Update role for a member, enforcing transactional last-owner protection."""
        valid_roles = {r.value for r in Role}
        if new_role.upper() not in valid_roles:
            raise ProblemException(
                status_code=400,
                title="Invalid Role",
                code="INVALID_ROLE",
                detail=f"Role '{new_role}' is not a recognized organization role.",
            )

        stmt = (
            sa.select(Membership)
            .where(
                Membership.tenant_id == tenant_id,
                Membership.user_id == target_user_id,
                Membership.status == "ACTIVE",
            )
            .with_for_update()
        )
        membership = uow.session.scalar(stmt)
        if membership is None:
            raise ProblemException(
                status_code=404,
                title="Member Not Found",
                code="MEMBER_NOT_FOUND",
                detail="Active member not found in this organization.",
            )

        # Invariant: Last Owner Protection
        if membership.role == Role.OWNER.value and new_role.upper() != Role.OWNER.value:
            other_owners = uow.session.scalar(
                sa.select(sa.func.count(Membership.id)).where(
                    Membership.tenant_id == tenant_id,
                    Membership.role == Role.OWNER.value,
                    Membership.status == "ACTIVE",
                    Membership.user_id != target_user_id,
                )
            )
            if not other_owners or other_owners < 1:
                raise ProblemException(
                    status_code=409,
                    title="Last Owner Protection",
                    code="LAST_OWNER_PROTECTION",
                    detail="Cannot demote the last active owner of an organization.",
                )

        old_role = membership.role
        membership.role = new_role.upper()
        membership.version += 1
        uow.session.flush()

        uow.record_audit_event(
            action="MEMBER_ROLE_UPDATED",
            resource_type="membership",
            resource_id=membership.id,
            payload={
                "target_user_id": str(target_user_id),
                "old_role": old_role,
                "new_role": membership.role,
            },
        )
        return membership

    def remove_member(
        self,
        uow: UnitOfWork,
        tenant_id: UUID,
        target_user_id: UUID,
    ) -> None:
        """Offboard member: revokes membership, terminates sessions, enforces last-owner."""
        stmt = (
            sa.select(Membership)
            .where(
                Membership.tenant_id == tenant_id,
                Membership.user_id == target_user_id,
                Membership.status == "ACTIVE",
            )
            .with_for_update()
        )
        membership = uow.session.scalar(stmt)
        if membership is None:
            raise ProblemException(
                status_code=404,
                title="Member Not Found",
                code="MEMBER_NOT_FOUND",
                detail="Active member not found in this organization.",
            )

        # Invariant: Last Owner Protection
        if membership.role == Role.OWNER.value:
            other_owners = uow.session.scalar(
                sa.select(sa.func.count(Membership.id)).where(
                    Membership.tenant_id == tenant_id,
                    Membership.role == Role.OWNER.value,
                    Membership.status == "ACTIVE",
                    Membership.user_id != target_user_id,
                )
            )
            if not other_owners or other_owners < 1:
                raise ProblemException(
                    status_code=409,
                    title="Last Owner Protection",
                    code="LAST_OWNER_PROTECTION",
                    detail="Cannot remove the last active owner of an organization.",
                )

        membership.status = "REVOKED"
        membership.version += 1
        uow.session.flush()

        # Emit outbox event for offboarding side-effects (disconnect OAuth, revoke approvals)
        uow.admit_operation(
            operation_type="OFFBOARD_MEMBER_CLEANUP",
            payload={"tenant_id": str(tenant_id), "user_id": str(target_user_id)},
            resource_id=membership.id,
        )

        uow.record_audit_event(
            action="MEMBER_OFFBOARDED",
            resource_type="membership",
            resource_id=membership.id,
            payload={"target_user_id": str(target_user_id), "role": membership.role},
        )

    def transfer_ownership(
        self,
        uow: UnitOfWork,
        tenant_id: UUID,
        current_owner_id: UUID,
        target_user_id: UUID,
    ) -> None:
        """Transfer organization ownership from current owner to target member."""
        if current_owner_id == target_user_id:
            return

        # Caller must be active owner
        owner_mem = uow.session.scalar(
            sa.select(Membership).where(
                Membership.tenant_id == tenant_id,
                Membership.user_id == current_owner_id,
                Membership.role == Role.OWNER.value,
                Membership.status == "ACTIVE",
            )
        )
        if owner_mem is None:
            raise ProblemException(
                status_code=403,
                title="Forbidden",
                code="OWNERSHIP_TRANSFER_UNAUTHORIZED",
                detail="Only the current active owner can initiate an ownership transfer.",
            )

        # Target must be active member
        target_mem = uow.session.scalar(
            sa.select(Membership).where(
                Membership.tenant_id == tenant_id,
                Membership.user_id == target_user_id,
                Membership.status == "ACTIVE",
            )
        )
        if target_mem is None:
            raise ProblemException(
                status_code=404,
                title="Target Member Not Found",
                code="TARGET_MEMBER_NOT_FOUND",
                detail="Target user must be an active member of this organization.",
            )

        # Atomic handoff
        owner_mem.role = Role.ADMIN.value
        owner_mem.version += 1
        target_mem.role = Role.OWNER.value
        target_mem.version += 1
        uow.session.flush()

        uow.record_audit_event(
            action="OWNERSHIP_TRANSFERRED",
            resource_type="organization",
            resource_id=tenant_id,
            payload={
                "previous_owner_id": str(current_owner_id),
                "new_owner_id": str(target_user_id),
            },
        )

    # --- Teams Management ---

    def create_team(
        self,
        uow: UnitOfWork,
        tenant_id: UUID,
        name: str,
        description: str | None = None,
    ) -> Team:
        """Create a new hiring team within the organization."""
        clean_name = name.strip()
        if not clean_name:
            raise ProblemException(
                status_code=400,
                title="Invalid Team Name",
                code="INVALID_TEAM_NAME",
                detail="Team name cannot be empty.",
            )

        existing = uow.session.scalar(
            sa.select(Team).where(Team.tenant_id == tenant_id, Team.name == clean_name)
        )
        if existing:
            raise ProblemException(
                status_code=409,
                title="Team Conflict",
                code="TEAM_NAME_CONFLICT",
                detail=f"Team '{clean_name}' already exists in this organization.",
            )

        team = Team(
            tenant_id=tenant_id,
            name=clean_name,
            description=description.strip() if description else None,
        )
        uow.session.add(team)
        uow.session.flush()

        uow.record_audit_event(
            action="TEAM_CREATED",
            resource_type="team",
            resource_id=team.id,
            payload={"name": team.name},
        )
        return team

    def list_teams(self, session: Session, tenant_id: UUID) -> list[Team]:
        """List all teams for the organization."""
        stmt = sa.select(Team).where(Team.tenant_id == tenant_id).order_by(Team.name.asc())
        return list(session.scalars(stmt).all())

    def add_team_member(
        self,
        uow: UnitOfWork,
        tenant_id: UUID,
        team_id: UUID,
        membership_id: UUID,
    ) -> TeamMember:
        """Assign an active member to a team."""
        team = uow.session.scalar(
            sa.select(Team).where(Team.tenant_id == tenant_id, Team.id == team_id)
        )
        if team is None:
            raise ProblemException(
                status_code=404,
                title="Team Not Found",
                code="TEAM_NOT_FOUND",
                detail="Team not found in this organization.",
            )

        membership = uow.session.scalar(
            sa.select(Membership).where(
                Membership.tenant_id == tenant_id,
                Membership.id == membership_id,
                Membership.status == "ACTIVE",
            )
        )
        if membership is None:
            raise ProblemException(
                status_code=404,
                title="Membership Not Found",
                code="MEMBERSHIP_NOT_FOUND",
                detail="Active membership not found in this organization.",
            )

        existing = uow.session.scalar(
            sa.select(TeamMember).where(
                TeamMember.tenant_id == tenant_id,
                TeamMember.team_id == team_id,
                TeamMember.membership_id == membership_id,
            )
        )
        if existing:
            return existing

        tm = TeamMember(
            tenant_id=tenant_id,
            team_id=team_id,
            membership_id=membership_id,
        )
        uow.session.add(tm)
        uow.session.flush()

        uow.record_audit_event(
            action="TEAM_MEMBER_ADDED",
            resource_type="team_member",
            resource_id=tm.id,
            payload={"team_id": str(team_id), "membership_id": str(membership_id)},
        )
        return tm

    def remove_team_member(
        self,
        uow: UnitOfWork,
        tenant_id: UUID,
        team_id: UUID,
        membership_id: UUID,
    ) -> None:
        """Remove a member from a team."""
        tm = uow.session.scalar(
            sa.select(TeamMember).where(
                TeamMember.tenant_id == tenant_id,
                TeamMember.team_id == team_id,
                TeamMember.membership_id == membership_id,
            )
        )
        if tm is not None:
            uow.session.delete(tm)
            uow.session.flush()
            uow.record_audit_event(
                action="TEAM_MEMBER_REMOVED",
                resource_type="team_member",
                resource_id=tm.id,
                payload={"team_id": str(team_id), "membership_id": str(membership_id)},
            )

    # --- Invitations Management ---

    def create_invitation(
        self,
        uow: UnitOfWork,
        tenant_id: UUID,
        invited_by_user_id: UUID,
        email: str,
        role: str,
        mail_provider: SystemMailProvider | None = None,
    ) -> tuple[MembershipInvitation, str]:
        """Create a single-use hashed invitation token and notify the invitee."""
        clean_email = email.lower().strip()
        valid_roles = {r.value for r in Role}
        if role.upper() not in valid_roles:
            raise ProblemException(
                status_code=400,
                title="Invalid Role",
                code="INVALID_ROLE",
                detail=f"Role '{role}' is not a valid organization role.",
            )

        # Check if user already holds active membership
        existing_user = uow.session.scalar(sa.select(User).where(User.email == clean_email))
        if existing_user:
            active_mem = uow.session.scalar(
                sa.select(Membership).where(
                    Membership.tenant_id == tenant_id,
                    Membership.user_id == existing_user.id,
                    Membership.status == "ACTIVE",
                )
            )
            if active_mem:
                raise ProblemException(
                    status_code=409,
                    title="User Already Member",
                    code="USER_ALREADY_MEMBER",
                    detail=f"User with email '{clean_email}' is already an active member of this organization.",
                )

        raw_token = generate_token_256()
        token_hash = hash_token(raw_token)
        now = self.clock.now()

        invitation = MembershipInvitation(
            tenant_id=tenant_id,
            email=clean_email,
            role=role.upper(),
            token_hash=token_hash,
            invited_by_user_id=invited_by_user_id,
            status="PENDING",
            expires_at=now + timedelta(days=7),
        )
        uow.session.add(invitation)
        uow.session.flush()

        # Enqueue system mail outbox command
        uow.admit_operation(
            operation_type="SEND_MEMBERSHIP_INVITATION_EMAIL",
            payload={
                "tenant_id": str(tenant_id),
                "invitation_id": str(invitation.id),
                "email": clean_email,
                "role": role.upper(),
            },
            resource_id=invitation.id,
        )

        # Dispatch immediate email if mail provider is configured
        if mail_provider is not None:
            mail_provider.send_system_email(
                recipient=clean_email,
                subject="You have been invited to join Talvora",
                body_text=(
                    f"You have been invited to join Talvora as {role.upper()}.\n"
                    f"Invitation token: {raw_token}\n"
                    "This invitation expires in 7 days."
                ),
            )

        uow.record_audit_event(
            action="INVITATION_CREATED",
            resource_type="membership_invitation",
            resource_id=invitation.id,
            payload={"email": clean_email, "role": role.upper()},
        )
        return invitation, raw_token

    def accept_invitation(
        self,
        uow: UnitOfWork,
        token: str,
        accepting_user_id: UUID,
    ) -> Membership:
        """Redeem a valid invitation token, creating an active organization membership."""
        token_hash = hash_token(token)
        now = self.clock.now()

        stmt = (
            sa.select(MembershipInvitation)
            .where(
                MembershipInvitation.token_hash == token_hash,
                MembershipInvitation.status == "PENDING",
            )
            .with_for_update()
        )
        inv = uow.session.scalar(stmt)
        if inv is None:
            raise ProblemException(
                status_code=400,
                title="Invalid Invitation",
                code="INVITATION_INVALID",
                detail="Invitation is invalid, expired, or already used.",
            )

        exp = inv.expires_at if inv.expires_at.tzinfo else inv.expires_at.replace(tzinfo=UTC)
        if exp < now:
            inv.status = "EXPIRED"
            uow.session.flush()
            raise ProblemException(
                status_code=400,
                title="Invitation Expired",
                code="INVITATION_EXPIRED",
                detail="This invitation has expired.",
            )

        user = uow.session.get(User, accepting_user_id)
        if user is None:
            raise ProblemException(
                status_code=401,
                title="User Not Found",
                code="USER_NOT_FOUND",
                detail="Authenticated user identity not found.",
            )

        # Email match verification: invitee must match user email
        if user.email.lower().strip() != inv.email.lower().strip():
            raise ProblemException(
                status_code=403,
                title="Email Mismatch",
                code="INVITATION_EMAIL_MISMATCH",
                detail="Logged in email does not match the invitation email.",
            )

        uow.tenant_id = inv.tenant_id

        # Check if already a member
        existing = uow.session.scalar(
            sa.select(Membership).where(
                Membership.tenant_id == inv.tenant_id,
                Membership.user_id == accepting_user_id,
            )
        )
        if existing:
            existing.role = inv.role
            existing.status = "ACTIVE"
            existing.version += 1
            membership = existing
        else:
            membership = Membership(
                tenant_id=inv.tenant_id,
                user_id=accepting_user_id,
                role=inv.role,
                status="ACTIVE",
            )
            uow.session.add(membership)

        inv.status = "ACCEPTED"
        inv.accepted_at = now
        uow.session.flush()

        uow.record_audit_event(
            action="INVITATION_ACCEPTED",
            resource_type="membership_invitation",
            resource_id=inv.id,
            payload={"tenant_id": str(inv.tenant_id), "user_id": str(accepting_user_id)},
        )
        return membership
