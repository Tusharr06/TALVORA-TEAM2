"""Candidates Application Service: Tenant-Local Identity, Contact Revisions, Verifications."""

import base64
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import func, or_, select

from talvora.modules.candidates.domain.models import (
    Candidate,
    CandidateContactEvent,
    CandidateContactRevision,
    CandidateContactVerification,
    CandidateSource,
    ContactEventType,
    ContactSource,
    ContactVerificationSource,
    mask_email,
    normalize_email,
    normalize_name,
    sanitize_text,
    validate_email_shape,
)
from talvora.modules.identity.domain.roles import Capability
from talvora.shared.application.actor import ActorContext
from talvora.shared.infrastructure.uow import UnitOfWork
from talvora.shared.transport.problems import ProblemException


def _utc_now() -> datetime:
    return datetime.now(UTC)


class CandidateService:
    """Manages tenant-local candidate identity, contact revisions, and verifications."""

    # ------------------------------------------------------------------
    # Shared guards
    # ------------------------------------------------------------------

    @classmethod
    def _verify_tenant_actor(cls, actor: ActorContext) -> UUID:
        if not actor.is_authenticated or actor.tenant_id is None or actor.user_id is None:
            raise ProblemException(
                status_code=401,
                title="Unauthorized",
                code="UNAUTHENTICATED",
                detail="Valid session authentication within an organization is required.",
            )
        return actor.tenant_id

    @classmethod
    def _require_capability(cls, actor: ActorContext, capability: Capability) -> None:
        if not actor.has_capability(capability.value):
            raise ProblemException(
                status_code=403,
                title="Forbidden",
                code="CAPABILITY_DENIED",
                detail=f"Actor lacks capability '{capability.value}' required for this operation.",
            )

    # ------------------------------------------------------------------
    # Candidate CRUD
    # ------------------------------------------------------------------

    @classmethod
    def create_candidate(
        cls,
        uow: UnitOfWork,
        actor: ActorContext,
        display_name: str | None = None,
        email: str | None = None,
        phone: str | None = None,
        source: CandidateSource = CandidateSource.MANUAL_ENTRY,
        is_draft: bool = False,
    ) -> Candidate:
        """Create a candidate identity. Draft upload-intake candidates require no name/address."""
        tenant_id = cls._verify_tenant_actor(actor)
        cls._require_capability(actor, Capability.CREATE_CANDIDATE)

        clean_name = sanitize_text(display_name) if display_name else None
        clean_email: str | None = None
        if email:
            clean_email = validate_email_shape(sanitize_text(email) or email)

        if not is_draft and not clean_name and not clean_email:
            raise ProblemException(
                status_code=422,
                title="Validation Error",
                code="VALIDATION_ERROR",
                detail=(
                    "A non-draft candidate requires at least a display name or an email. "
                    "Use is_draft=true for unverified upload-intake candidates."
                ),
            )

        now = _utc_now()
        candidate = Candidate(
            id=uuid4(),
            tenant_id=tenant_id,
            display_name=clean_name,
            normalized_name=normalize_name(clean_name) if clean_name else None,
            email=clean_email,
            normalized_email=normalize_email(clean_email) if clean_email else None,
            phone=phone.strip() if phone else None,
            source=source.value,
            is_draft=is_draft,
            current_contact_revision_number=0,
            created_by=actor.user_id,
            version=1,
            created_at=now,
            updated_at=now,
        )
        uow.session.add(candidate)
        uow.session.flush()

        if clean_name or clean_email or phone:
            contact_source = (
                ContactSource.SYSTEM_IMPORT if is_draft else ContactSource.RECRUITER_ENTERED
            )
            cls._append_contact_revision(
                uow,
                tenant_id=tenant_id,
                candidate=candidate,
                display_name=clean_name,
                email=clean_email,
                phone=candidate.phone,
                source=contact_source,
                actor_id=actor.user_id,
                now=now,
            )

        uow.record_audit_event(
            actor_id=actor.user_id,
            action="CANDIDATE_CREATED",
            resource_type="candidate",
            resource_id=candidate.id,
            payload={"is_draft": is_draft, "source": source.value},
        )

        return candidate

    @classmethod
    def create_draft_for_intake(
        cls,
        uow: UnitOfWork,
        actor: ActorContext,
        display_name: str | None = None,
        email: str | None = None,
        phone: str | None = None,
    ) -> Candidate:
        """Create an unverified draft candidate for upload intake. No invented values."""
        return cls.create_candidate(
            uow=uow,
            actor=actor,
            display_name=display_name,
            email=email,
            phone=phone,
            source=CandidateSource.UPLOAD_INTAKE,
            is_draft=True,
        )

    @classmethod
    def _append_contact_revision(
        cls,
        uow: UnitOfWork,
        tenant_id: UUID,
        candidate: Candidate,
        display_name: str | None,
        email: str | None,
        phone: str | None,
        source: ContactSource,
        actor_id: UUID,
        now: datetime,
    ) -> CandidateContactRevision:
        next_number = candidate.current_contact_revision_number + 1
        revision = CandidateContactRevision(
            id=uuid4(),
            tenant_id=tenant_id,
            candidate_id=candidate.id,
            revision_number=next_number,
            display_name=display_name,
            normalized_name=normalize_name(display_name) if display_name else None,
            email=email,
            normalized_email=normalize_email(email) if email else None,
            phone=phone,
            source=source.value,
            created_by=actor_id,
            created_at=now,
        )
        uow.session.add(revision)
        uow.session.flush()

        previous_verified = candidate.verified_contact_revision_number
        candidate.current_contact_revision_id = revision.id
        candidate.current_contact_revision_number = next_number

        event = CandidateContactEvent(
            id=uuid4(),
            tenant_id=tenant_id,
            candidate_id=candidate.id,
            event_type=ContactEventType.CONTACT_REVISED.value,
            contact_revision_id=revision.id,
            contact_revision_number=next_number,
            previous_verified_revision_number=previous_verified,
            actor_id=actor_id,
            created_at=now,
        )
        uow.session.add(event)
        uow.record_outbox_event(topic="candidates.contact.revised", payload_ref=event.id)

        return revision

    @classmethod
    def get_candidate(cls, uow: UnitOfWork, actor: ActorContext, candidate_id: UUID) -> Candidate:
        """Fetch a candidate with tenant-scoped resource concealment."""
        tenant_id = cls._verify_tenant_actor(actor)
        cls._require_capability(actor, Capability.VIEW_CANDIDATES)

        candidate = uow.session.scalar(
            select(Candidate).where(Candidate.tenant_id == tenant_id, Candidate.id == candidate_id)
        )
        if not candidate:
            raise ProblemException(
                status_code=404,
                title="Resource Not Found",
                code="RESOURCE_NOT_FOUND",
                detail="The requested candidate was not found.",
            )
        return candidate

    @classmethod
    def get_candidates_by_ids(
        cls, uow: UnitOfWork, actor: ActorContext, candidate_ids: list[UUID]
    ) -> dict[UUID, Candidate]:
        """Public use-case: batch-fetch candidates by ID within the actor's tenant."""
        tenant_id = cls._verify_tenant_actor(actor)
        if not candidate_ids:
            return {}
        stmt = select(Candidate).where(
            Candidate.tenant_id == tenant_id, Candidate.id.in_(set(candidate_ids))
        )
        return {c.id: c for c in uow.session.scalars(stmt).all()}

    @classmethod
    def list_candidates(
        cls,
        uow: UnitOfWork,
        actor: ActorContext,
        email: str | None = None,
        name: str | None = None,
        cursor: str | None = None,
        limit: int = 20,
    ) -> tuple[list[Candidate], str | None, int]:
        """List/search candidates by tenant-local normalized email/name with cursor pagination."""
        tenant_id = cls._verify_tenant_actor(actor)
        cls._require_capability(actor, Capability.VIEW_CANDIDATES)

        limit = max(1, min(limit, 100))
        stmt = select(Candidate).where(Candidate.tenant_id == tenant_id)

        if email:
            stmt = stmt.where(Candidate.normalized_email == normalize_email(email))
        if name:
            stmt = stmt.where(Candidate.normalized_name == normalize_name(name))

        count_stmt = select(func.count()).select_from(stmt.subquery())
        total_count = uow.session.scalar(count_stmt) or 0

        if cursor:
            try:
                decoded = base64.b64decode(cursor.encode()).decode()
                raw_val, raw_id = decoded.split("|", 1)
                cursor_id = UUID(raw_id)
                cursor_dt = datetime.fromisoformat(raw_val)
                stmt = stmt.where(
                    (Candidate.created_at < cursor_dt)
                    | ((Candidate.created_at == cursor_dt) & (Candidate.id < cursor_id))
                )
            except Exception:
                pass

        stmt = stmt.order_by(Candidate.created_at.desc(), Candidate.id.desc()).limit(limit + 1)
        results = list(uow.session.scalars(stmt).all())

        next_cursor = None
        if len(results) > limit:
            results = results[:limit]
            last_item = results[-1]
            next_cursor = base64.b64encode(
                f"{last_item.created_at.isoformat()}|{last_item.id}".encode()
            ).decode()

        return results, next_cursor, total_count

    @classmethod
    def find_duplicate_suggestions(
        cls,
        uow: UnitOfWork,
        actor: ActorContext,
        email: str | None = None,
        name: str | None = None,
        exclude_candidate_id: UUID | None = None,
        limit: int = 5,
    ) -> list[dict[str, object]]:
        """Conservative, minimum-exposure duplicate suggestions by exact normalized match.

        Never merges identities. Only a recruiter may act on a suggestion by selecting an
        existing candidate when resolving an intake item (enforced by the caller).
        """
        tenant_id = cls._verify_tenant_actor(actor)
        cls._require_capability(actor, Capability.VIEW_CANDIDATES)

        norm_email = normalize_email(email) if email else None
        norm_name = normalize_name(name) if name else None
        if not norm_email and not norm_name:
            return []

        stmt = select(Candidate).where(Candidate.tenant_id == tenant_id)
        if exclude_candidate_id is not None:
            stmt = stmt.where(Candidate.id != exclude_candidate_id)

        conditions = []
        if norm_email:
            conditions.append(Candidate.normalized_email == norm_email)
        if norm_name:
            conditions.append(Candidate.normalized_name == norm_name)

        stmt = stmt.where(or_(*conditions)).order_by(Candidate.created_at.desc()).limit(limit)
        matches = list(uow.session.scalars(stmt).all())

        suggestions: list[dict[str, object]] = []
        for m in matches:
            suggestions.append(
                {
                    "candidate_id": m.id,
                    "display_name": m.display_name,
                    "masked_email": mask_email(m.email) if m.email else None,
                    "is_draft": m.is_draft,
                    "created_at": m.created_at,
                    "matched_on": (
                        "email" if norm_email and m.normalized_email == norm_email else "name"
                    ),
                }
            )
        return suggestions

    @classmethod
    def update_candidate(
        cls,
        uow: UnitOfWork,
        actor: ActorContext,
        candidate_id: UUID,
        display_name: str | None = None,
        email: str | None = None,
        phone: str | None = None,
        expected_version: int | None = None,
    ) -> Candidate:
        """Update candidate contact/display details, appending a new contact revision."""
        tenant_id = cls._verify_tenant_actor(actor)
        cls._require_capability(actor, Capability.EDIT_CANDIDATE)

        candidate = uow.session.scalar(
            select(Candidate)
            .where(Candidate.tenant_id == tenant_id, Candidate.id == candidate_id)
            .with_for_update()
        )
        if not candidate:
            raise ProblemException(
                status_code=404,
                title="Resource Not Found",
                code="RESOURCE_NOT_FOUND",
                detail="The requested candidate was not found.",
            )

        if expected_version is not None and candidate.version != expected_version:
            raise ProblemException(
                status_code=409,
                title="Version Conflict",
                code="VERSION_CONFLICT",
                detail=(
                    f"Candidate was modified concurrently. Expected version "
                    f"{expected_version}, but current version is {candidate.version}."
                ),
            )

        clean_name = sanitize_text(display_name) if display_name is not None else None
        clean_email: str | None = None
        if email is not None and email != "":
            clean_email = validate_email_shape(sanitize_text(email) or email)

        contact_changed = False
        next_name = candidate.display_name
        next_email = candidate.email
        next_phone = candidate.phone

        if display_name is not None:
            next_name = clean_name
            contact_changed = True
        if email is not None:
            next_email = clean_email
            contact_changed = True
        if phone is not None:
            next_phone = phone.strip() if phone else None
            contact_changed = True

        if contact_changed:
            now = _utc_now()
            cls._append_contact_revision(
                uow,
                tenant_id=tenant_id,
                candidate=candidate,
                display_name=next_name,
                email=next_email,
                phone=next_phone,
                source=ContactSource.RECRUITER_ENTERED,
                actor_id=actor.user_id,
                now=now,
            )
            candidate.display_name = next_name
            candidate.normalized_name = normalize_name(next_name) if next_name else None
            candidate.email = next_email
            candidate.normalized_email = normalize_email(next_email) if next_email else None
            candidate.phone = next_phone
            candidate.updated_at = now

        if candidate.is_draft and (candidate.display_name or candidate.email):
            candidate.is_draft = False

        candidate.version += 1

        uow.record_audit_event(
            actor_id=actor.user_id,
            action="CANDIDATE_UPDATED",
            resource_type="candidate",
            resource_id=candidate.id,
            payload={"version": candidate.version, "contact_changed": contact_changed},
        )

        return candidate

    # ------------------------------------------------------------------
    # Contact Verification
    # ------------------------------------------------------------------

    @classmethod
    def verify_contact(
        cls,
        uow: UnitOfWork,
        actor: ActorContext,
        candidate_id: UUID,
        contact_revision_number: int,
        verified_address: str,
        verification_source: ContactVerificationSource,
        notes: str | None = None,
        expected_version: int | None = None,
    ) -> CandidateContactVerification:
        """Record a human command attesting to an exact contact address and version.

        This is NOT proof of mailbox ownership; it is an auditable human decision bound
        to a specific contact revision version.
        """
        tenant_id = cls._verify_tenant_actor(actor)
        cls._require_capability(actor, Capability.VERIFY_CANDIDATE_CONTACT)

        if not actor.is_human:
            raise ProblemException(
                status_code=403,
                title="Forbidden",
                code="CAPABILITY_DENIED",
                detail="Only an accountable human actor may record a contact verification.",
            )

        candidate = uow.session.scalar(
            select(Candidate)
            .where(Candidate.tenant_id == tenant_id, Candidate.id == candidate_id)
            .with_for_update()
        )
        if not candidate:
            raise ProblemException(
                status_code=404,
                title="Resource Not Found",
                code="RESOURCE_NOT_FOUND",
                detail="The requested candidate was not found.",
            )

        if expected_version is not None and candidate.version != expected_version:
            raise ProblemException(
                status_code=409,
                title="Version Conflict",
                code="VERSION_CONFLICT",
                detail=(
                    f"Candidate was modified concurrently. Expected version "
                    f"{expected_version}, but current version is {candidate.version}."
                ),
            )

        revision = uow.session.scalar(
            select(CandidateContactRevision).where(
                CandidateContactRevision.tenant_id == tenant_id,
                CandidateContactRevision.candidate_id == candidate_id,
                CandidateContactRevision.revision_number == contact_revision_number,
            )
        )
        if not revision:
            raise ProblemException(
                status_code=422,
                title="Invalid Verified Contact",
                code="EVIDENCE_INVALID",
                detail=f"No contact revision {contact_revision_number} exists for this candidate.",
            )

        clean_address = validate_email_shape(verified_address)
        if (
            revision.normalized_email
            and normalize_email(clean_address) != revision.normalized_email
        ):
            raise ProblemException(
                status_code=422,
                title="Invalid Verified Contact",
                code="EVIDENCE_INVALID",
                detail=(
                    "The verified address does not match the email on the referenced "
                    "contact revision."
                ),
            )

        now = _utc_now()
        verification = CandidateContactVerification(
            id=uuid4(),
            tenant_id=tenant_id,
            candidate_id=candidate_id,
            contact_revision_id=revision.id,
            contact_revision_number=revision.revision_number,
            verified_address=clean_address,
            verification_source=verification_source.value,
            notes=sanitize_text(notes) if notes else None,
            actor_id=actor.user_id,
            created_at=now,
        )
        uow.session.add(verification)

        previous_verified = candidate.verified_contact_revision_number
        candidate.verified_contact_revision_id = revision.id
        candidate.verified_contact_revision_number = revision.revision_number
        candidate.version += 1
        candidate.updated_at = now

        event = CandidateContactEvent(
            id=uuid4(),
            tenant_id=tenant_id,
            candidate_id=candidate_id,
            event_type=ContactEventType.CONTACT_VERIFIED.value,
            contact_revision_id=revision.id,
            contact_revision_number=revision.revision_number,
            previous_verified_revision_number=previous_verified,
            actor_id=actor.user_id,
            created_at=now,
        )
        uow.session.add(event)
        uow.record_outbox_event(topic="candidates.contact.verified", payload_ref=event.id)

        uow.record_audit_event(
            actor_id=actor.user_id,
            action="CANDIDATE_CONTACT_VERIFIED",
            resource_type="candidate",
            resource_id=candidate.id,
            payload={
                "contact_revision_number": revision.revision_number,
                "verification_source": verification_source.value,
            },
        )

        return verification
