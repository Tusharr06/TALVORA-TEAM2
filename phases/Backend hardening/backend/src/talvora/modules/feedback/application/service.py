"""Application Services for Blind Structured Feedback and Controlled DOCX/PDF Exports (P16)."""

import hashlib
from dataclasses import dataclass
from datetime import UTC, timedelta
from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect
from sqlalchemy import select

from talvora.modules.applications.domain.models import Application, ApplicationStage
from talvora.modules.candidates.domain.models import Candidate
from talvora.modules.documents.application.ports import ObjectStoragePort
from talvora.modules.evidence.domain.models import EvidenceFragment, SourceSegment
from talvora.modules.feedback.application.exporters import (
    ExportCitationItem,
    ExportFeedbackItem,
    PackExportPayload,
    render_pack_docx,
    render_pack_pdf,
)
from talvora.modules.feedback.domain.models import (
    EXPORT_REVOCATION_DISCLOSURE,
    CompetencyRating,
    ExportArtifact,
    ExportContentScope,
    ExportFormat,
    ExportStatus,
    Feedback,
    FeedbackRevision,
    FeedbackStatus,
    HumanRecommendation,
    validate_interview_observation_text,
    validate_rating_value,
)
from talvora.modules.identity.application.authorization import AuthorizationService
from talvora.modules.identity.domain.roles import Capability, Role
from talvora.modules.interview_packs.application.ports import (
    DefaultInterviewContextAdapter,
    InterviewContextPort,
    InterviewSnapshot,
)
from talvora.modules.interview_packs.domain.models import (
    Competency,
    InterviewContextStatus,
    PackClaim,
    PackQuestion,
    PackRevision,
    PackRevisionStatus,
)
from talvora.modules.jobs.domain.models import Job, JobAssignment, JobRole
from talvora.modules.organizations.domain.models import Membership
from talvora.shared.application.actor import ActorContext
from talvora.shared.infrastructure.uow import UnitOfWork
from talvora.shared.transport.problems import ProblemException

DOCX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
PDF_MEDIA_TYPE = "application/pdf"


@dataclass(frozen=True)
class CompetencyRatingInputSpec:
    competency_id: UUID
    rating: str
    observation: str = ""
    question_notes: str | None = None


@dataclass(frozen=True)
class FeedbackRevisionDetail:
    revision: FeedbackRevision
    ratings: list[CompetencyRating]


@dataclass(frozen=True)
class FeedbackAggregateBundle:
    feedback: Feedback
    revisions: list[FeedbackRevisionDetail]
    include_draft_fields: bool


def _require_active_member(uow: UnitOfWork, actor: ActorContext) -> tuple[UUID, UUID, Membership]:
    if not actor.is_authenticated or actor.user_id is None:
        raise ProblemException(
            status_code=401,
            title="Unauthorized",
            code="AUTH_REQUIRED",
            detail="Authenticated session is required.",
        )
    if actor.tenant_id is None:
        raise ProblemException(
            status_code=403,
            title="Forbidden",
            code="FORBIDDEN",
            detail="Active organization membership is required; offboarded or unselected members cannot access feedback or exports.",
        )
    membership = uow.session.scalar(
        select(Membership).where(
            Membership.tenant_id == actor.tenant_id,
            Membership.user_id == actor.user_id,
        )
    )
    if membership is None or membership.status != "ACTIVE":
        raise ProblemException(
            status_code=403,
            title="Forbidden",
            code="FORBIDDEN",
            detail="Active organization membership is required; offboarded or suspended members cannot access or mutate feedback/exports.",
        )
    return actor.tenant_id, actor.user_id, membership


def _is_job_reviewer(
    uow: UnitOfWork,
    actor: ActorContext,
    membership: Membership,
    tenant_id: UUID,
    job_id: UUID,
) -> bool:
    if membership.role in {Role.OWNER.value, Role.ADMIN.value}:
        return True
    assignments = list(
        uow.session.scalars(
            select(JobAssignment).where(
                JobAssignment.tenant_id == tenant_id,
                JobAssignment.job_id == job_id,
            )
        ).all()
    )
    return any(
        a.user_id == actor.user_id
        and a.role in {JobRole.RECRUITER.value, JobRole.HIRING_MANAGER.value}
        for a in assignments
    )


def _read_feedback_and_export_policy(uow: UnitOfWork, tenant_id: UUID) -> tuple[bool, bool, int]:
    """Returns `(reveal_peer_after_submit, allow_blind_override, export_ttl_hours)`."""
    try:
        tables = set(sa_inspect(uow.session.connection()).get_table_names())
    except Exception:
        return False, False, 24

    if "organization_policy" not in tables:
        return False, False, 24

    row = (
        uow.session.execute(
            sa.text(
                "SELECT reveal_peer_feedback_after_submit, allow_blind_feedback_override, "
                "export_ttl_hours FROM organization_policy WHERE tenant_id = :tid"
            ),
            {"tid": str(tenant_id)},
        )
        .mappings()
        .first()
    )
    if row is None:
        return False, False, 24

    return (
        bool(row["reveal_peer_feedback_after_submit"]),
        bool(row["allow_blind_feedback_override"]),
        int(row["export_ttl_hours"] or 24),
    )


class FeedbackService:
    """Manages interviewer-owned drafts, immutable submissions, amendments, and blind visibility."""

    @classmethod
    def _resolve_interview_and_published_pack(
        cls,
        uow: UnitOfWork,
        tenant_id: UUID,
        interview_id: UUID,
        interview_port: InterviewContextPort | None = None,
    ) -> tuple[InterviewSnapshot, Application, PackRevision]:
        port = interview_port or DefaultInterviewContextAdapter()
        interview = port.get_interview(uow.session, tenant_id, interview_id)
        if interview is None:
            raise ProblemException(
                status_code=404,
                title="Resource Not Found",
                code="NOT_FOUND",
                detail="The requested interview was not found.",
            )
        app_row = uow.session.scalar(
            select(Application).where(
                Application.tenant_id == tenant_id,
                Application.id == interview.application_id,
            )
        )
        if app_row is None:
            raise ProblemException(
                status_code=404,
                title="Resource Not Found",
                code="NOT_FOUND",
                detail="Interview application was not found.",
            )
        pub_pack = uow.session.scalar(
            select(PackRevision)
            .where(
                PackRevision.tenant_id == tenant_id,
                PackRevision.interview_id == interview_id,
                PackRevision.status == PackRevisionStatus.PUBLISHED.value,
            )
            .order_by(PackRevision.revision_number.desc())
            .limit(1)
        )
        if pub_pack is None:
            raise ProblemException(
                status_code=409,
                title="Published Interview Pack Required",
                code="INVALID_TRANSITION",
                detail="A published interview pack revision is required before recording structured feedback.",
            )
        return interview, app_row, pub_pack

    @staticmethod
    def _ensure_interviewer_participant(user_id: UUID, interview: InterviewSnapshot) -> None:
        if user_id not in interview.participant_user_ids:
            raise ProblemException(
                status_code=403,
                title="Forbidden",
                code="FORBIDDEN",
                detail="Only an assigned interview participant may author or submit feedback for this interview.",
            )

    @staticmethod
    def _ensure_mutable_interview_state(app_row: Application, interview: InterviewSnapshot) -> None:
        if app_row.stage == ApplicationStage.WITHDRAWN.value:
            raise ProblemException(
                status_code=409,
                title="Application Withdrawn",
                code="INVALID_TRANSITION",
                detail="Cannot edit or submit feedback for a withdrawn application.",
            )
        if interview.status == InterviewContextStatus.CANCELED.value:
            raise ProblemException(
                status_code=409,
                title="Interview Canceled",
                code="INVALID_TRANSITION",
                detail="Cannot edit or submit feedback for a canceled interview.",
            )

    @staticmethod
    def _load_framework_competencies(
        uow: UnitOfWork, tenant_id: UUID, framework_version_id: UUID
    ) -> dict[UUID, Competency]:
        rows = list(
            uow.session.scalars(
                select(Competency).where(
                    Competency.tenant_id == tenant_id,
                    Competency.framework_version_id == framework_version_id,
                )
            ).all()
        )
        return {c.id: c for c in rows}

    @classmethod
    def _validate_ratings_against_framework(
        cls,
        uow: UnitOfWork,
        tenant_id: UUID,
        framework_version_id: UUID,
        ratings: list[CompetencyRatingInputSpec],
        require_complete_submission: bool,
    ) -> list[CompetencyRatingInputSpec]:
        comp_map = cls._load_framework_competencies(uow, tenant_id, framework_version_id)
        seen: set[UUID] = set()
        normalized: list[CompetencyRatingInputSpec] = []

        for item in ratings:
            if item.competency_id not in comp_map:
                raise ProblemException(
                    status_code=422,
                    title="Framework Competency Mismatch",
                    code="VALIDATION_ERROR",
                    detail=(
                        f"Competency {item.competency_id} does not belong to pinned "
                        f"framework version {framework_version_id}."
                    ),
                )
            if item.competency_id in seen:
                raise ProblemException(
                    status_code=422,
                    title="Duplicate Competency Rating",
                    code="VALIDATION_ERROR",
                    detail=f"Duplicate rating supplied for competency {item.competency_id}.",
                )
            seen.add(item.competency_id)
            clean_rating = validate_rating_value(item.rating)
            clean_obs = (
                validate_interview_observation_text(item.observation, "competency observation")
                if item.observation
                else ""
            )
            clean_qnotes = (
                validate_interview_observation_text(item.question_notes, "question notes")
                if item.question_notes
                else None
            )

            comp_row = comp_map[item.competency_id]
            if (
                require_complete_submission
                and comp_row.observation_required
                and clean_rating != "NOT_ASSESSED"
                and not clean_obs
            ):
                raise ProblemException(
                    status_code=422,
                    title="Observation Required",
                    code="VALIDATION_ERROR",
                    detail=(
                        f"Competency '{comp_row.code}' requires an observable behavioral note "
                        "when rated 1-4."
                    ),
                )
            normalized.append(
                CompetencyRatingInputSpec(
                    competency_id=item.competency_id,
                    rating=clean_rating,
                    observation=clean_obs,
                    question_notes=clean_qnotes,
                )
            )

        if require_complete_submission and seen != set(comp_map.keys()):
            missing_codes = sorted(comp_map[cid].code for cid in (set(comp_map.keys()) - seen))
            raise ProblemException(
                status_code=422,
                title="Incomplete Competency Ratings",
                code="VALIDATION_ERROR",
                detail=(
                    f"Submission must explicitly account for all framework competencies "
                    f"(use NOT_ASSESSED where uncovered; missing={missing_codes})."
                ),
            )
        return normalized

    @classmethod
    def get_my_feedback(
        cls,
        uow: UnitOfWork,
        actor: ActorContext,
        interview_id: UUID,
        interview_port: InterviewContextPort | None = None,
    ) -> FeedbackAggregateBundle:
        tenant_id, user_id, _ = _require_active_member(uow, actor)
        AuthorizationService.enforce_capability(actor, Capability.SUBMIT_FEEDBACK)

        interview, _, pub_pack = cls._resolve_interview_and_published_pack(
            uow, tenant_id, interview_id, interview_port
        )
        cls._ensure_interviewer_participant(user_id, interview)

        fb = uow.session.scalar(
            select(Feedback).where(
                Feedback.tenant_id == tenant_id,
                Feedback.interview_id == interview_id,
                Feedback.interviewer_user_id == user_id,
            )
        )
        if fb is None:
            fb = Feedback(
                id=uuid4(),
                tenant_id=tenant_id,
                interview_id=interview_id,
                application_id=interview.application_id,
                job_id=interview.job_id,
                candidate_id=interview.candidate_id,
                interviewer_user_id=user_id,
                pack_revision_id=pub_pack.id,
                framework_version_id=pub_pack.framework_version_id,
                status=FeedbackStatus.DRAFT.value,
                draft_ratings=[],
                version=1,
            )
            uow.session.add(fb)
            uow.session.flush()

        return cls._hydrate_bundle(uow, tenant_id, fb, include_draft_fields=True)

    @classmethod
    def save_my_draft(
        cls,
        uow: UnitOfWork,
        actor: ActorContext,
        interview_id: UUID,
        expected_version: int | None,
        overall_observations: str | None,
        human_recommendation: str | None,
        ratings: list[CompetencyRatingInputSpec],
        interview_port: InterviewContextPort | None = None,
    ) -> FeedbackAggregateBundle:
        tenant_id, user_id, _ = _require_active_member(uow, actor)
        AuthorizationService.enforce_capability(actor, Capability.SUBMIT_FEEDBACK)

        interview, app_row, pub_pack = cls._resolve_interview_and_published_pack(
            uow, tenant_id, interview_id, interview_port
        )
        cls._ensure_interviewer_participant(user_id, interview)
        cls._ensure_mutable_interview_state(app_row, interview)

        fb = uow.session.scalar(
            select(Feedback).where(
                Feedback.tenant_id == tenant_id,
                Feedback.interview_id == interview_id,
                Feedback.interviewer_user_id == user_id,
            )
        )
        if fb is None:
            fb = Feedback(
                id=uuid4(),
                tenant_id=tenant_id,
                interview_id=interview_id,
                application_id=interview.application_id,
                job_id=interview.job_id,
                candidate_id=interview.candidate_id,
                interviewer_user_id=user_id,
                pack_revision_id=pub_pack.id,
                framework_version_id=pub_pack.framework_version_id,
                status=FeedbackStatus.DRAFT.value,
                draft_ratings=[],
                version=1,
            )
            uow.session.add(fb)
            uow.session.flush()
        else:
            if fb.status != FeedbackStatus.DRAFT.value:
                raise ProblemException(
                    status_code=409,
                    title="Feedback Already Submitted",
                    code="INVALID_TRANSITION",
                    detail=(
                        "Submitted feedback is immutable; use the explicit /amend command "
                        "with an amendment reason to record a new revision."
                    ),
                )
            if expected_version is not None and fb.version != expected_version:
                raise ProblemException(
                    status_code=409,
                    title="Version Conflict",
                    code="VERSION_CONFLICT",
                    detail=f"Expected feedback lock version {expected_version}, found {fb.version}.",
                )

        norm_ratings = cls._validate_ratings_against_framework(
            uow=uow,
            tenant_id=tenant_id,
            framework_version_id=fb.framework_version_id,
            ratings=ratings,
            require_complete_submission=False,
        )
        clean_overall = (
            validate_interview_observation_text(overall_observations, "overall observations")
            if overall_observations is not None
            else None
        )
        rec = (human_recommendation or HumanRecommendation.NONE.value).strip().upper()
        if rec not in {r.value for r in HumanRecommendation}:
            raise ProblemException(
                status_code=422,
                title="Invalid Recommendation",
                code="VALIDATION_ERROR",
                detail=f"Unsupported human recommendation '{rec}'.",
            )

        fb.draft_overall_observations = clean_overall
        fb.draft_human_recommendation = rec
        fb.draft_ratings = [
            {
                "competency_id": str(r.competency_id),
                "rating": r.rating,
                "observation": r.observation,
                "question_notes": r.question_notes,
            }
            for r in norm_ratings
        ]
        fb.version += 1
        fb.updated_at = uow.clock.now()
        uow.session.flush()

        uow.record_audit(
            resource_type="feedback",
            action="FEEDBACK_DRAFT_SAVED",
            resource_id=fb.id,
            actor_id=user_id,
            actor_capability=Capability.SUBMIT_FEEDBACK.value,
            details={"interview_id": str(interview_id), "version": fb.version},
        )
        return cls._hydrate_bundle(uow, tenant_id, fb, include_draft_fields=True)

    @classmethod
    def submit_my_feedback(
        cls,
        uow: UnitOfWork,
        actor: ActorContext,
        interview_id: UUID,
        expected_version: int | None = None,
        overall_observations: str | None = None,
        human_recommendation: str | None = None,
        ratings: list[CompetencyRatingInputSpec] | None = None,
        interview_port: InterviewContextPort | None = None,
    ) -> FeedbackAggregateBundle:
        """Submits immutable revision #1 of an interviewer's feedback. Never modifies pipeline stage."""
        tenant_id, user_id, _ = _require_active_member(uow, actor)
        AuthorizationService.enforce_capability(actor, Capability.SUBMIT_FEEDBACK)

        interview, app_row, pub_pack = cls._resolve_interview_and_published_pack(
            uow, tenant_id, interview_id, interview_port
        )
        cls._ensure_interviewer_participant(user_id, interview)
        cls._ensure_mutable_interview_state(app_row, interview)

        fb = uow.session.scalar(
            select(Feedback).where(
                Feedback.tenant_id == tenant_id,
                Feedback.interview_id == interview_id,
                Feedback.interviewer_user_id == user_id,
            )
        )
        if fb is None:
            fb = Feedback(
                id=uuid4(),
                tenant_id=tenant_id,
                interview_id=interview_id,
                application_id=interview.application_id,
                job_id=interview.job_id,
                candidate_id=interview.candidate_id,
                interviewer_user_id=user_id,
                pack_revision_id=pub_pack.id,
                framework_version_id=pub_pack.framework_version_id,
                status=FeedbackStatus.DRAFT.value,
                draft_ratings=[],
                version=1,
            )
            uow.session.add(fb)
            uow.session.flush()
        else:
            if fb.status != FeedbackStatus.DRAFT.value:
                raise ProblemException(
                    status_code=409,
                    title="Duplicate Feedback Submission",
                    code="INVALID_TRANSITION",
                    detail="Feedback has already been submitted; use /amend with a reason to append a new revision.",
                )
            if expected_version is not None and fb.version != expected_version:
                raise ProblemException(
                    status_code=409,
                    title="Version Conflict",
                    code="VERSION_CONFLICT",
                    detail=f"Expected feedback lock version {expected_version}, found {fb.version}.",
                )

        effective_ratings = (
            ratings
            if ratings is not None
            else [
                CompetencyRatingInputSpec(
                    competency_id=UUID(str(d["competency_id"])),
                    rating=str(d["rating"]),
                    observation=str(d.get("observation") or ""),
                    question_notes=d.get("question_notes"),
                )
                for d in (fb.draft_ratings or [])
            ]
        )
        effective_overall_raw = (
            overall_observations
            if overall_observations is not None
            else (fb.draft_overall_observations or "")
        )
        clean_overall = validate_interview_observation_text(
            effective_overall_raw, "overall observations"
        )
        if not clean_overall:
            raise ProblemException(
                status_code=422,
                title="Missing Overall Observations",
                code="VALIDATION_ERROR",
                detail="Overall observable interview summary is required to submit feedback.",
            )

        rec = (
            (
                human_recommendation
                or fb.draft_human_recommendation
                or HumanRecommendation.NONE.value
            )
            .strip()
            .upper()
        )
        if rec not in {r.value for r in HumanRecommendation}:
            raise ProblemException(
                status_code=422,
                title="Invalid Recommendation",
                code="VALIDATION_ERROR",
                detail=f"Unsupported human recommendation '{rec}'.",
            )

        norm_ratings = cls._validate_ratings_against_framework(
            uow=uow,
            tenant_id=tenant_id,
            framework_version_id=fb.framework_version_id,
            ratings=effective_ratings,
            require_complete_submission=True,
        )

        now = uow.clock.now()
        is_late = False
        if interview.scheduled_end_at is not None:
            end_utc = (
                interview.scheduled_end_at.replace(tzinfo=UTC)
                if interview.scheduled_end_at.tzinfo is None
                else interview.scheduled_end_at
            )
            if now > end_utc + timedelta(hours=48):
                is_late = True

        rev = FeedbackRevision(
            id=uuid4(),
            tenant_id=tenant_id,
            feedback_id=fb.id,
            revision_number=1,
            pack_revision_id=fb.pack_revision_id,
            framework_version_id=fb.framework_version_id,
            overall_observations=clean_overall,
            human_recommendation=rec,
            amendment_reason=None,
            prior_revision_id=None,
            is_late_submission=is_late,
            submitted_by=user_id,
            submitted_at=now,
        )
        uow.session.add(rev)
        uow.session.flush()

        for r in norm_ratings:
            uow.session.add(
                CompetencyRating(
                    id=uuid4(),
                    tenant_id=tenant_id,
                    feedback_revision_id=rev.id,
                    competency_id=r.competency_id,
                    rating=r.rating,
                    observation=r.observation,
                    question_notes=r.question_notes,
                )
            )

        fb.status = FeedbackStatus.SUBMITTED.value
        fb.latest_submitted_revision = 1
        fb.first_submitted_at = now
        fb.last_submitted_at = now
        fb.version += 1
        fb.updated_at = now
        uow.session.flush()

        uow.record_audit(
            resource_type="feedback_revision",
            action="FEEDBACK_SUBMITTED",
            resource_id=rev.id,
            actor_id=user_id,
            actor_capability=Capability.SUBMIT_FEEDBACK.value,
            details={
                "feedback_id": str(fb.id),
                "interview_id": str(interview_id),
                "revision_number": 1,
                "is_late_submission": is_late,
            },
        )
        return cls._hydrate_bundle(uow, tenant_id, fb, include_draft_fields=True)

    @classmethod
    def amend_my_feedback(
        cls,
        uow: UnitOfWork,
        actor: ActorContext,
        interview_id: UUID,
        expected_version: int,
        amendment_reason: str,
        overall_observations: str,
        human_recommendation: str,
        ratings: list[CompetencyRatingInputSpec],
        interview_port: InterviewContextPort | None = None,
    ) -> FeedbackAggregateBundle:
        """Appends a new immutable revision while retaining all prior submitted revisions."""
        tenant_id, user_id, _ = _require_active_member(uow, actor)
        AuthorizationService.enforce_capability(actor, Capability.SUBMIT_FEEDBACK)

        interview, app_row, _ = cls._resolve_interview_and_published_pack(
            uow, tenant_id, interview_id, interview_port
        )
        cls._ensure_interviewer_participant(user_id, interview)
        cls._ensure_mutable_interview_state(app_row, interview)

        fb = uow.session.scalar(
            select(Feedback).where(
                Feedback.tenant_id == tenant_id,
                Feedback.interview_id == interview_id,
                Feedback.interviewer_user_id == user_id,
            )
        )
        if fb is None or fb.latest_submitted_revision < 1:
            raise ProblemException(
                status_code=409,
                title="Feedback Not Yet Submitted",
                code="INVALID_TRANSITION",
                detail="Cannot amend feedback before an initial submission exists.",
            )
        if fb.version != expected_version:
            raise ProblemException(
                status_code=409,
                title="Version Conflict",
                code="VERSION_CONFLICT",
                detail=f"Expected feedback lock version {expected_version}, found {fb.version}.",
            )

        clean_reason = validate_interview_observation_text(amendment_reason, "amendment reason")
        if len(clean_reason) < 5:
            raise ProblemException(
                status_code=422,
                title="Amendment Reason Required",
                code="VALIDATION_ERROR",
                detail="Amendments require an explicit reason of at least 5 characters.",
            )
        clean_overall = validate_interview_observation_text(
            overall_observations, "overall observations"
        )
        if not clean_overall:
            raise ProblemException(
                status_code=422,
                title="Missing Overall Observations",
                code="VALIDATION_ERROR",
                detail="Overall observations cannot be empty.",
            )
        rec = human_recommendation.strip().upper()
        if rec not in {r.value for r in HumanRecommendation}:
            raise ProblemException(
                status_code=422,
                title="Invalid Recommendation",
                code="VALIDATION_ERROR",
                detail=f"Unsupported human recommendation '{rec}'.",
            )

        norm_ratings = cls._validate_ratings_against_framework(
            uow=uow,
            tenant_id=tenant_id,
            framework_version_id=fb.framework_version_id,
            ratings=ratings,
            require_complete_submission=True,
        )

        prior_rev = uow.session.scalar(
            select(FeedbackRevision).where(
                FeedbackRevision.tenant_id == tenant_id,
                FeedbackRevision.feedback_id == fb.id,
                FeedbackRevision.revision_number == fb.latest_submitted_revision,
            )
        )
        next_rev_num = fb.latest_submitted_revision + 1
        now = uow.clock.now()

        rev = FeedbackRevision(
            id=uuid4(),
            tenant_id=tenant_id,
            feedback_id=fb.id,
            revision_number=next_rev_num,
            pack_revision_id=fb.pack_revision_id,
            framework_version_id=fb.framework_version_id,
            overall_observations=clean_overall,
            human_recommendation=rec,
            amendment_reason=clean_reason,
            prior_revision_id=prior_rev.id if prior_rev else None,
            is_late_submission=True,
            submitted_by=user_id,
            submitted_at=now,
        )
        uow.session.add(rev)
        uow.session.flush()

        for r in norm_ratings:
            uow.session.add(
                CompetencyRating(
                    id=uuid4(),
                    tenant_id=tenant_id,
                    feedback_revision_id=rev.id,
                    competency_id=r.competency_id,
                    rating=r.rating,
                    observation=r.observation,
                    question_notes=r.question_notes,
                )
            )

        fb.status = FeedbackStatus.AMENDED.value
        fb.latest_submitted_revision = next_rev_num
        fb.last_submitted_at = now
        fb.version += 1
        fb.updated_at = now
        uow.session.flush()

        uow.record_audit(
            resource_type="feedback_revision",
            action="FEEDBACK_AMENDED",
            resource_id=rev.id,
            actor_id=user_id,
            actor_capability=Capability.SUBMIT_FEEDBACK.value,
            details={
                "feedback_id": str(fb.id),
                "interview_id": str(interview_id),
                "revision_number": next_rev_num,
                "prior_revision_id": str(prior_rev.id) if prior_rev else None,
            },
        )
        return cls._hydrate_bundle(uow, tenant_id, fb, include_draft_fields=True)

    @classmethod
    def list_interview_feedback(
        cls,
        uow: UnitOfWork,
        actor: ActorContext,
        interview_id: UUID,
        use_blind_override: bool = False,
        interview_port: InterviewContextPort | None = None,
    ) -> list[FeedbackAggregateBundle]:
        """Server-side blind-feedback enforcement across staff and participant queries."""
        tenant_id, user_id, membership = _require_active_member(uow, actor)
        AuthorizationService.enforce_capability(actor, Capability.READ_APPLICATION)

        port = interview_port or DefaultInterviewContextAdapter()
        interview = port.get_interview(uow.session, tenant_id, interview_id)
        if interview is None:
            raise ProblemException(
                status_code=404,
                title="Resource Not Found",
                code="NOT_FOUND",
                detail="The requested interview was not found.",
            )

        is_reviewer = _is_job_reviewer(uow, actor, membership, tenant_id, interview.job_id)
        is_participant = user_id in interview.participant_user_ids

        if not is_reviewer and not is_participant:
            raise ProblemException(
                status_code=404,
                title="Resource Not Found",
                code="NOT_FOUND",
                detail="The requested interview was not found.",
            )

        reveal_peer_after_submit, allow_blind_override, _ = _read_feedback_and_export_policy(
            uow, tenant_id
        )

        all_rows = list(
            uow.session.scalars(
                select(Feedback)
                .where(
                    Feedback.tenant_id == tenant_id,
                    Feedback.interview_id == interview_id,
                )
                .order_by(Feedback.created_at.asc())
            ).all()
        )
        my_fb = next((r for r in all_rows if r.interviewer_user_id == user_id), None)
        my_has_submitted = my_fb is not None and my_fb.latest_submitted_revision >= 1

        # Determine whether peer submitted feedback may be included
        can_see_peers = False
        if is_participant and not my_has_submitted:
            # Current actor is an interviewer on this panel and has NOT submitted yet -> blind!
            if use_blind_override and is_reviewer and allow_blind_override:
                can_see_peers = True
                uow.record_audit(
                    resource_type="interview_feedback",
                    action="BLIND_FEEDBACK_OVERRIDE_ACCESSED",
                    resource_id=interview_id,
                    actor_id=user_id,
                    actor_capability=Capability.READ_APPLICATION.value,
                    details={"interview_id": str(interview_id)},
                )
        elif is_reviewer or is_participant and my_has_submitted and reveal_peer_after_submit:
            can_see_peers = True

        visible_rows: list[tuple[Feedback, bool]] = []
        for row in all_rows:
            if row.interviewer_user_id == user_id:
                visible_rows.append((row, True))
            elif can_see_peers and row.latest_submitted_revision >= 1:
                # Peer drafts are NEVER exposed; only submitted revisions
                visible_rows.append((row, False))

        return cls._hydrate_bundles_batch(uow, tenant_id, visible_rows)

    @classmethod
    def _hydrate_bundles_batch(
        cls,
        uow: UnitOfWork,
        tenant_id: UUID,
        rows_with_draft_flag: list[tuple[Feedback, bool]],
    ) -> list[FeedbackAggregateBundle]:
        if not rows_with_draft_flag:
            return []

        fb_ids = [fb.id for fb, _ in rows_with_draft_flag]
        all_revs = list(
            uow.session.scalars(
                select(FeedbackRevision)
                .where(
                    FeedbackRevision.tenant_id == tenant_id,
                    FeedbackRevision.feedback_id.in_(fb_ids),
                )
                .order_by(
                    FeedbackRevision.feedback_id.asc(),
                    FeedbackRevision.revision_number.asc(),
                )
            ).all()
        )

        rev_ids = [rev.id for rev in all_revs]
        ratings_by_rev: dict[UUID, list[CompetencyRating]] = {rid: [] for rid in rev_ids}
        if rev_ids:
            all_ratings = list(
                uow.session.scalars(
                    select(CompetencyRating).where(
                        CompetencyRating.tenant_id == tenant_id,
                        CompetencyRating.feedback_revision_id.in_(rev_ids),
                    )
                ).all()
            )
            for rating in all_ratings:
                ratings_by_rev.setdefault(rating.feedback_revision_id, []).append(rating)

        revs_by_fb: dict[UUID, list[FeedbackRevisionDetail]] = {fid: [] for fid in fb_ids}
        for rev in all_revs:
            revs_by_fb.setdefault(rev.feedback_id, []).append(
                FeedbackRevisionDetail(
                    revision=rev,
                    ratings=ratings_by_rev.get(rev.id, []),
                )
            )

        return [
            FeedbackAggregateBundle(
                feedback=fb,
                revisions=revs_by_fb.get(fb.id, []),
                include_draft_fields=include_draft_fields,
            )
            for fb, include_draft_fields in rows_with_draft_flag
        ]

    @classmethod
    def _hydrate_bundle(
        cls,
        uow: UnitOfWork,
        tenant_id: UUID,
        fb: Feedback,
        include_draft_fields: bool,
    ) -> FeedbackAggregateBundle:
        return cls._hydrate_bundles_batch(uow, tenant_id, [(fb, include_draft_fields)])[0]


class PackExportService:
    """Generates, stores, re-authorizes, and serves time-limited private DOCX/PDF exports."""

    @classmethod
    def create_pack_export(
        cls,
        uow: UnitOfWork,
        actor: ActorContext,
        pack_revision_id: UUID,
        export_format: str,
        content_scope: str,
        storage: ObjectStoragePort,
        bucket_name: str,
        interview_port: InterviewContextPort | None = None,
        max_render_seconds: float = 15.0,
    ) -> ExportArtifact:
        tenant_id, user_id, membership = _require_active_member(uow, actor)
        AuthorizationService.enforce_capability(actor, Capability.READ_APPLICATION)

        fmt = export_format.strip().upper()
        scope = content_scope.strip().upper()
        if fmt not in {ExportFormat.DOCX.value, ExportFormat.PDF.value}:
            raise ProblemException(
                status_code=422,
                title="Invalid Export Format",
                code="VALIDATION_ERROR",
                detail="Export format must be DOCX or PDF.",
            )
        if scope not in {
            ExportContentScope.INTERVIEWER_PACK.value,
            ExportContentScope.REVIEWER_PACK_WITH_FEEDBACK.value,
        }:
            raise ProblemException(
                status_code=422,
                title="Invalid Export Scope",
                code="VALIDATION_ERROR",
                detail="Export content_scope must be INTERVIEWER_PACK or REVIEWER_PACK_WITH_FEEDBACK.",
            )

        pack_rev = uow.session.scalar(
            select(PackRevision).where(
                PackRevision.tenant_id == tenant_id,
                PackRevision.id == pack_revision_id,
            )
        )
        if pack_rev is None:
            raise ProblemException(
                status_code=404,
                title="Resource Not Found",
                code="NOT_FOUND",
                detail="Interview pack revision was not found.",
            )

        port = interview_port or DefaultInterviewContextAdapter()
        interview = port.get_interview(uow.session, tenant_id, pack_rev.interview_id)
        if interview is None:
            raise ProblemException(
                status_code=404,
                title="Resource Not Found",
                code="NOT_FOUND",
                detail="Interview was not found.",
            )

        is_reviewer = _is_job_reviewer(uow, actor, membership, tenant_id, pack_rev.job_id)
        is_participant = user_id in interview.participant_user_ids

        if not is_reviewer and not is_participant:
            raise ProblemException(
                status_code=403,
                title="Forbidden",
                code="FORBIDDEN",
                detail="Only assigned interview participants or authorized job reviewers may export this interview pack.",
            )
        if pack_rev.status != PackRevisionStatus.PUBLISHED.value:
            if not is_reviewer:
                raise ProblemException(
                    status_code=404,
                    title="Resource Not Found",
                    code="NOT_FOUND",
                    detail="Interview pack revision was not found.",
                )
            raise ProblemException(
                status_code=409,
                title="Pack Revision Not Published",
                code="CONFLICT",
                detail="Only human-reviewed and published interview pack revisions may be exported.",
            )
        if scope == ExportContentScope.REVIEWER_PACK_WITH_FEEDBACK.value and not is_reviewer:
            raise ProblemException(
                status_code=403,
                title="Forbidden",
                code="FORBIDDEN",
                detail="Only authorized job reviewers can export combined feedback packets.",
            )

        job = uow.session.scalar(
            select(Job).where(Job.tenant_id == tenant_id, Job.id == pack_rev.job_id)
        )
        cand = uow.session.scalar(
            select(Candidate).where(
                Candidate.tenant_id == tenant_id, Candidate.id == pack_rev.candidate_id
            )
        )
        if job is None or cand is None:
            raise ProblemException(
                status_code=404,
                title="Resource Not Found",
                code="NOT_FOUND",
                detail="Job or candidate could not be resolved for export.",
            )

        questions = list(
            uow.session.scalars(
                select(PackQuestion)
                .where(
                    PackQuestion.tenant_id == tenant_id,
                    PackQuestion.pack_revision_id == pack_rev.id,
                )
                .order_by(PackQuestion.order_index.asc())
            ).all()
        )
        claims = list(
            uow.session.scalars(
                select(PackClaim)
                .where(
                    PackClaim.tenant_id == tenant_id,
                    PackClaim.pack_revision_id == pack_rev.id,
                )
                .order_by(PackClaim.claim_index.asc())
            ).all()
        )

        # Gather unique cited fragments cleanly (no S3 keys/URLs!)
        cited_fids: list[UUID] = []
        seen_fids: set[UUID] = set()
        for cl in claims:
            for raw_id in cl.evidence_fragment_ids or []:
                uid = UUID(str(raw_id))
                if uid not in seen_fids:
                    seen_fids.add(uid)
                    cited_fids.append(uid)
        for q in questions:
            for raw_id in q.evidence_fragment_ids or []:
                uid = UUID(str(raw_id))
                if uid not in seen_fids:
                    seen_fids.add(uid)
                    cited_fids.append(uid)

        citations: list[ExportCitationItem] = []
        for fid in cited_fids:
            row = uow.session.execute(
                select(EvidenceFragment, SourceSegment)
                .join(
                    SourceSegment,
                    sa.and_(
                        SourceSegment.tenant_id == EvidenceFragment.tenant_id,
                        SourceSegment.id == EvidenceFragment.source_segment_id,
                    ),
                )
                .where(
                    EvidenceFragment.tenant_id == tenant_id,
                    EvidenceFragment.id == fid,
                )
            ).first()
            if row is not None:
                frag, seg = row[0], row[1]
                loc = (
                    f"PDF page {seg.page_number}"
                    if seg.page_number is not None
                    else f"DOCX {seg.docx_path or seg.locator_type}"
                )
                citations.append(
                    ExportCitationItem(
                        fragment_id=frag.id,
                        locator_label=loc,
                        quote=frag.quote,
                    )
                )

        # Optional feedback export strictly filtered through server-side blind-feedback rules
        feedback_items: list[ExportFeedbackItem] = []
        if scope == ExportContentScope.REVIEWER_PACK_WITH_FEEDBACK.value:
            visible_fb = FeedbackService.list_interview_feedback(
                uow=uow,
                actor=actor,
                interview_id=pack_rev.interview_id,
                use_blind_override=False,
                interview_port=interview_port,
            )
            comp_map = FeedbackService._load_framework_competencies(
                uow, tenant_id, pack_rev.framework_version_id
            )
            for idx, fb_bundle in enumerate(visible_fb, start=1):
                if not fb_bundle.revisions:
                    continue
                latest_detail = fb_bundle.revisions[-1]
                rev_row = latest_detail.revision
                feedback_items.append(
                    ExportFeedbackItem(
                        interviewer_label=f"Interviewer #{idx} ({rev_row.submitted_by})",
                        revision_number=rev_row.revision_number,
                        submitted_at_iso=rev_row.submitted_at.isoformat(),
                        human_recommendation=rev_row.human_recommendation,
                        overall_observations=rev_row.overall_observations,
                        ratings=tuple(
                            (
                                comp_map[r.competency_id].code
                                if r.competency_id in comp_map
                                else str(r.competency_id),
                                r.rating,
                                r.observation,
                            )
                            for r in latest_detail.ratings
                        ),
                    )
                )

        now = uow.clock.now()
        render_payload = PackExportPayload(
            job_title=job.title,
            candidate_display_name=cand.display_name or "Unnamed Candidate",
            stage=pack_rev.stage,
            duration_minutes=pack_rev.duration_minutes,
            pack_revision_id=pack_rev.id,
            pack_revision_number=pack_rev.revision_number,
            framework_version_id=pack_rev.framework_version_id,
            requirement_version_id=pack_rev.requirement_version_id,
            document_version_id=pack_rev.document_version_id,
            published_by=pack_rev.published_by,
            published_at=pack_rev.published_at,
            generated_at=now,
            candidate_overview=pack_rev.candidate_overview,
            candidate_evidence_summary=pack_rev.candidate_evidence_summary,
            interviewer_instructions=pack_rev.interviewer_instructions,
            notes_guidance=pack_rev.notes_guidance,
            time_allocation=dict(pack_rev.time_allocation or {}),
            questions=[
                {
                    "category": q.category,
                    "premise_classification": q.premise_classification,
                    "allocated_minutes": q.allocated_minutes,
                    "prompt_text": q.prompt_text,
                    "expected_signals": list(q.expected_signals or []),
                }
                for q in questions
            ],
            rubric=list(pack_rev.evaluation_rubric or []),
            citations=citations,
            feedback_items=feedback_items,
            max_render_seconds=max_render_seconds,
        )

        if fmt == ExportFormat.DOCX.value:
            rendered_bytes = render_pack_docx(render_payload)
            media_type = DOCX_MEDIA_TYPE
            ext = "docx"
        else:
            rendered_bytes = render_pack_pdf(render_payload)
            media_type = PDF_MEDIA_TYPE
            ext = "pdf"

        sha256_hex = hashlib.sha256(rendered_bytes).hexdigest()
        opaque_key = f"exports/{tenant_id}/{uuid4()}.{ext}"
        storage.put_object(
            bucket=bucket_name,
            key=opaque_key,
            body=rendered_bytes,
            content_type=media_type,
            sha256_checksum=sha256_hex,
        )

        _, _, ttl_hours = _read_feedback_and_export_policy(uow, tenant_id)
        artifact = ExportArtifact(
            id=uuid4(),
            tenant_id=tenant_id,
            pack_revision_id=pack_rev.id,
            interview_id=pack_rev.interview_id,
            application_id=pack_rev.application_id,
            job_id=pack_rev.job_id,
            candidate_id=pack_rev.candidate_id,
            format=fmt,
            content_scope=scope,
            status=ExportStatus.READY.value,
            operation_id=None,
            storage_bucket=bucket_name,
            storage_key=opaque_key,
            sha256_checksum=sha256_hex,
            byte_size=len(rendered_bytes),
            media_type=media_type,
            requested_by=user_id,
            expires_at=now + timedelta(hours=ttl_hours),
            created_at=now,
        )
        uow.session.add(artifact)
        uow.session.flush()

        # Record export usage if P17 usage_event exists
        try:
            if "usage_event" in set(sa_inspect(uow.session.connection()).get_table_names()):
                uow.session.execute(
                    sa.text(
                        "INSERT INTO usage_event "
                        "(id, tenant_id, category, model_or_engine, input_units, output_units, "
                        "total_units, resource_type, resource_id, recorded_at) "
                        "VALUES (:id, :tid, 'EXPORT_GENERATION', :fmt, 1, :sz, 1, 'export_artifact', :rid, :ts)"
                    ),
                    {
                        "id": str(uuid4()),
                        "tid": str(tenant_id),
                        "fmt": fmt,
                        "sz": len(rendered_bytes),
                        "rid": str(artifact.id),
                        "ts": now,
                    },
                )
        except Exception:
            pass

        uow.record_audit(
            resource_type="export_artifact",
            action="EXPORT_GENERATED",
            resource_id=artifact.id,
            actor_id=user_id,
            actor_capability=Capability.READ_APPLICATION.value,
            details={
                "pack_revision_id": str(pack_rev.id),
                "format": fmt,
                "content_scope": scope,
                "byte_size": len(rendered_bytes),
                "expires_at": artifact.expires_at.isoformat(),
            },
        )
        return artifact

    @classmethod
    def get_export_metadata(
        cls,
        uow: UnitOfWork,
        actor: ActorContext,
        export_id: UUID,
        interview_port: InterviewContextPort | None = None,
    ) -> ExportArtifact:
        tenant_id, user_id, membership = _require_active_member(uow, actor)
        AuthorizationService.enforce_capability(actor, Capability.READ_APPLICATION)

        artifact = uow.session.scalar(
            select(ExportArtifact).where(
                ExportArtifact.tenant_id == tenant_id,
                ExportArtifact.id == export_id,
            )
        )
        if artifact is None:
            raise ProblemException(
                status_code=404,
                title="Resource Not Found",
                code="NOT_FOUND",
                detail="Export artifact was not found.",
            )

        is_reviewer = _is_job_reviewer(uow, actor, membership, tenant_id, artifact.job_id)
        if not is_reviewer:
            port = interview_port or DefaultInterviewContextAdapter()
            interview = port.get_interview(uow.session, tenant_id, artifact.interview_id)
            if (
                interview is None
                or user_id not in interview.participant_user_ids
                or artifact.content_scope != ExportContentScope.INTERVIEWER_PACK.value
            ):
                raise ProblemException(
                    status_code=404,
                    title="Resource Not Found",
                    code="NOT_FOUND",
                    detail="Export artifact was not found.",
                )
        return artifact

    @classmethod
    def download_export(
        cls,
        uow: UnitOfWork,
        actor: ActorContext,
        export_id: UUID,
        storage: ObjectStoragePort,
        interview_port: InterviewContextPort | None = None,
    ) -> tuple[ExportArtifact, bytes]:
        """Re-authorizes membership, assignment, revocation, and 24h TTL before streaming bytes."""
        artifact = cls.get_export_metadata(uow, actor, export_id, interview_port)
        tenant_id = artifact.tenant_id
        now = uow.clock.now()

        if artifact.status == ExportStatus.REVOKED.value or artifact.revoked_at is not None:
            raise ProblemException(
                status_code=410,
                title="Export Revoked",
                code="INVALID_TRANSITION",
                detail="This export artifact has been revoked under retention or compliance policy.",
            )

        expires_utc = (
            artifact.expires_at.replace(tzinfo=UTC)
            if artifact.expires_at.tzinfo is None
            else artifact.expires_at
        )
        if now >= expires_utc or artifact.status == ExportStatus.EXPIRED.value:
            artifact.status = ExportStatus.EXPIRED.value
            uow.session.flush()
            raise ProblemException(
                status_code=410,
                title="Export Expired",
                code="INVALID_TRANSITION",
                detail="This time-limited export artifact has expired (24-hour TTL). Generate a fresh export if authorized.",
            )

        # Verify underlying candidate still exists (not purged mid-window)
        cand = uow.session.scalar(
            select(Candidate).where(
                Candidate.tenant_id == tenant_id,
                Candidate.id == artifact.candidate_id,
            )
        )
        if cand is None:
            artifact.status = ExportStatus.REVOKED.value
            artifact.revoked_at = now
            artifact.revoked_reason = "CANDIDATE_PURGED"
            uow.session.flush()
            raise ProblemException(
                status_code=410,
                title="Export Unavailable",
                code="NOT_FOUND",
                detail="Candidate records for this export are no longer retained.",
            )

        stream = storage.get_object_stream(artifact.storage_bucket, artifact.storage_key)
        try:
            body = stream.read()
        finally:
            stream.close()

        actual_sha = hashlib.sha256(body).hexdigest()
        if actual_sha != artifact.sha256_checksum:
            raise ProblemException(
                status_code=503,
                title="Export Integrity Failure",
                code="DOCUMENT_UNREADABLE",
                detail="Stored export checksum verification failed.",
            )

        uow.record_audit(
            resource_type="export_artifact",
            action="EXPORT_DOWNLOADED",
            resource_id=artifact.id,
            actor_id=actor.user_id,
            actor_capability=Capability.READ_APPLICATION.value,
            details={
                "format": artifact.format,
                "disclosure_acknowledged": EXPORT_REVOCATION_DISCLOSURE,
            },
        )
        return artifact, body

    @classmethod
    def revoke_export(
        cls,
        uow: UnitOfWork,
        actor: ActorContext,
        export_id: UUID,
        reason: str,
        storage: ObjectStoragePort,
    ) -> ExportArtifact:
        tenant_id, user_id, membership = _require_active_member(uow, actor)
        AuthorizationService.enforce_capability(actor, Capability.READ_APPLICATION)

        artifact = uow.session.scalar(
            select(ExportArtifact).where(
                ExportArtifact.tenant_id == tenant_id,
                ExportArtifact.id == export_id,
            )
        )
        if artifact is None:
            raise ProblemException(
                status_code=404,
                title="Resource Not Found",
                code="NOT_FOUND",
                detail="Export artifact was not found.",
            )
        if not _is_job_reviewer(uow, actor, membership, tenant_id, artifact.job_id):
            raise ProblemException(
                status_code=403,
                title="Forbidden",
                code="FORBIDDEN",
                detail="Only authorized job reviewers or owners can revoke exports.",
            )

        storage.delete_object(artifact.storage_bucket, artifact.storage_key)
        artifact.status = ExportStatus.REVOKED.value
        artifact.revoked_at = uow.clock.now()
        artifact.revoked_reason = validate_interview_observation_text(reason, "revocation reason")
        uow.session.flush()

        uow.record_audit(
            resource_type="export_artifact",
            action="EXPORT_REVOKED",
            resource_id=artifact.id,
            actor_id=user_id,
            actor_capability=Capability.READ_APPLICATION.value,
            details={"reason": artifact.revoked_reason},
        )
        return artifact
