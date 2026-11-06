"""Deterministic tri-state screening and append-only recruiter overrides."""

import re
from uuid import UUID, uuid4

from sqlalchemy import select

from talvora.modules.applications.domain.models import Application
from talvora.modules.documents.domain.models import ApplicationDocument, ApplicationDocumentStatus
from talvora.modules.evidence.domain.models import EvidenceFragment
from talvora.modules.facts.domain.models import (
    CandidateFactEvidence,
    CandidateFactPolarity,
    CandidateFactRevision,
    CandidateFactStatus,
    CandidateFactType,
)
from talvora.modules.identity.domain.roles import Capability, Role
from talvora.modules.jobs.domain.models import Criterion, CriterionType, Job, RequirementVersion
from talvora.modules.screening.domain.models import (
    ScreeningOutcome,
    ScreeningOverride,
    ScreeningResult,
    ScreeningResultFact,
    ScreeningRun,
)
from talvora.shared.application.actor import ActorContext
from talvora.shared.infrastructure.uow import UnitOfWork
from talvora.shared.transport.problems import ProblemException

_RULE_VERSION = "deterministic-1"
_DEGREE_ORDER = {
    "HIGH_SCHOOL": 1,
    "HIGH SCHOOL": 1,
    "SECONDARY": 1,
    "ASSOCIATES": 2,
    "ASSOCIATE": 2,
    "BACHELORS": 3,
    "BACHELOR": 3,
    "MASTER": 4,
    "MASTERS": 4,
    "DOCTORATE": 5,
    "PHD": 5,
}


class ScreeningService:
    """Evaluates only explicit, current facts against a published criteria snapshot."""

    @staticmethod
    def _authorize(actor: ActorContext) -> UUID:
        if not actor.is_authenticated or actor.tenant_id is None or actor.user_id is None:
            raise ProblemException(
                status_code=401,
                title="Unauthorized",
                code="UNAUTHENTICATED",
                detail="An authenticated organization member is required.",
            )
        if (
            not actor.has_capability(Capability.READ_APPLICATION.value)
            or not actor.has_capability(Capability.READ_DOCUMENTS.value)
            or (actor.membership and actor.membership.role == Role.INTERVIEWER.value)
        ):
            raise ProblemException(
                status_code=403,
                title="Forbidden",
                code="CAPABILITY_DENIED",
                detail="Screening requires application and document review access.",
            )
        return actor.tenant_id

    @staticmethod
    def _latest_facts(
        uow: UnitOfWork,
        tenant_id: UUID,
        candidate_id: UUID,
        document_version_id: UUID | None,
    ) -> list[CandidateFactRevision]:
        if document_version_id is None:
            return []
        fact_ids = (
            select(CandidateFactEvidence.fact_revision_id)
            .join(
                EvidenceFragment,
                (EvidenceFragment.tenant_id == CandidateFactEvidence.tenant_id)
                & (EvidenceFragment.id == CandidateFactEvidence.evidence_fragment_id),
            )
            .where(
                CandidateFactEvidence.tenant_id == tenant_id,
                EvidenceFragment.document_version_id == document_version_id,
            )
        )
        revisions = uow.session.scalars(
            select(CandidateFactRevision)
            .where(
                CandidateFactRevision.tenant_id == tenant_id,
                CandidateFactRevision.candidate_id == candidate_id,
                CandidateFactRevision.id.in_(fact_ids),
            )
            .order_by(
                CandidateFactRevision.fact_key,
                CandidateFactRevision.revision_number.desc(),
            )
        ).all()
        latest: dict[UUID, CandidateFactRevision] = {}
        for revision in revisions:
            latest.setdefault(revision.fact_key, revision)
        return [
            fact for fact in latest.values() if fact.status != CandidateFactStatus.REJECTED.value
        ]

    @staticmethod
    def _fact_evidence_ids(
        uow: UnitOfWork,
        tenant_id: UUID,
        facts: list[CandidateFactRevision],
        document_version_id: UUID | None,
    ) -> list[UUID]:
        if not facts or document_version_id is None:
            return []
        return list(
            uow.session.scalars(
                select(CandidateFactEvidence.evidence_fragment_id).where(
                    CandidateFactEvidence.tenant_id == tenant_id,
                    CandidateFactEvidence.fact_revision_id.in_([fact.id for fact in facts]),
                    CandidateFactEvidence.evidence_fragment_id.in_(
                        select(EvidenceFragment.id).where(
                            EvidenceFragment.tenant_id == tenant_id,
                            EvidenceFragment.document_version_id == document_version_id,
                        )
                    ),
                )
            ).all()
        )

    @classmethod
    def _evaluate(
        cls,
        uow: UnitOfWork,
        tenant_id: UUID,
        criterion: Criterion,
        facts: list[CandidateFactRevision],
        document_version_id: UUID | None,
    ) -> tuple[ScreeningOutcome, str, list[CandidateFactRevision]]:
        if document_version_id is None:
            return (
                ScreeningOutcome.UNKNOWN,
                "No active application document is bound for screening.",
                [],
            )
        if criterion.requires_human_review:
            return ScreeningOutcome.UNKNOWN, "Criterion requires human review.", []

        config = criterion.configuration
        criterion_type = criterion.criterion_type
        reviewed_facts = [
            fact
            for fact in facts
            if fact.status
            in (CandidateFactStatus.VERIFIED.value, CandidateFactStatus.CORRECTED.value)
        ]

        if criterion_type == CriterionType.SKILL_PRESENT.value:
            if config.get("min_proficiency"):
                return (
                    ScreeningOutcome.UNKNOWN,
                    "Proficiency is not represented by the available structured facts.",
                    [],
                )
            accepted = {
                str(config.get("canonical_name") or "").casefold(),
                str(config.get("skill_name") or "").casefold(),
                *(str(item).casefold() for item in config.get("synonyms", [])),
            } - {""}
            matches = [
                fact
                for fact in facts
                if fact.fact_type == CandidateFactType.SKILL.value
                and fact.value.strip().casefold() in accepted
            ]
            if any(fact.status == CandidateFactStatus.EXTRACTED.value for fact in matches):
                return (
                    ScreeningOutcome.UNKNOWN,
                    "Skill evidence has not been human-reviewed.",
                    [],
                )
            used = [
                fact
                for fact in reviewed_facts
                if fact.fact_type == CandidateFactType.SKILL.value
                and fact.value.strip().casefold() in accepted
            ]
            polarities = {fact.polarity for fact in used}
            if len(polarities) > 1 or CandidateFactPolarity.UNKNOWN.value in polarities:
                return ScreeningOutcome.UNKNOWN, "Skill evidence is conflicting or uncertain.", []
            if used:
                evidence_ids = cls._fact_evidence_ids(uow, tenant_id, used, document_version_id)
                if not evidence_ids:
                    return ScreeningOutcome.UNKNOWN, "Matching fact has no source evidence.", []
                if polarities == {CandidateFactPolarity.NEGATED.value}:
                    return ScreeningOutcome.FAIL, "Cited human-reviewed evidence negates this skill.", used
                return ScreeningOutcome.PASS, "A cited human-reviewed fact affirms this skill.", used
            return (
                ScreeningOutcome.UNKNOWN,
                "No human-reviewed fact establishes this skill requirement.",
                [],
            )

        if criterion_type == CriterionType.MIN_EXPERIENCE_MONTHS.value:
            if config.get("relevant_titles") or config.get("relevant_skills"):
                return (
                    ScreeningOutcome.UNKNOWN,
                    "Relevant experience scope is not represented by the available facts.",
                    [],
                )
            all_experience_facts = [
                fact for fact in facts
                if fact.fact_type == CandidateFactType.EXPERIENCE_MONTHS.value
            ]
            if any(
                fact.status == CandidateFactStatus.EXTRACTED.value
                for fact in all_experience_facts
            ):
                return (
                    ScreeningOutcome.UNKNOWN,
                    "Experience evidence has not been human-reviewed.",
                    [],
                )
            matching_facts = [
                fact for fact in reviewed_facts
                if fact.fact_type == CandidateFactType.EXPERIENCE_MONTHS.value
            ]
            if any(
                fact.polarity != CandidateFactPolarity.AFFIRMED.value
                for fact in matching_facts
            ):
                return ScreeningOutcome.UNKNOWN, "Experience evidence is uncertain.", []
            parsed: list[tuple[CandidateFactRevision, int]] = []
            for fact in matching_facts:
                match = re.fullmatch(
                    r"\s*(\d+)\s*(?:months?|mos?)?\s*", fact.value, re.IGNORECASE
                )
                if match:
                    parsed.append((fact, int(match.group(1))))
            amounts = {amount for _, amount in parsed}
            if len(amounts) != 1:
                return ScreeningOutcome.UNKNOWN, (
                    "Experience evidence is missing, unparseable, or conflicting."
                ), []
            fact, amount = parsed[0]
            evidence_ids = cls._fact_evidence_ids(
                uow, tenant_id, [fact], document_version_id
            )
            if not evidence_ids:
                return ScreeningOutcome.UNKNOWN, "Experience fact has no source evidence.", []
            required = int(config.get("min_months", 0))
            explanation = f"Cited experience is {amount} months; {required} required."
            if amount >= required:
                return ScreeningOutcome.PASS, explanation, [fact]
            return ScreeningOutcome.FAIL, explanation, [fact]

        if criterion_type == CriterionType.EDUCATION_LEVEL.value:
            if config.get("acceptable_fields_of_study") or config.get(
                "allow_equivalent_experience"
            ):
                return (
                    ScreeningOutcome.UNKNOWN,
                    "Field-of-study or equivalent-experience constraints cannot be evaluated "
                    "from the available facts.",
                    [],
                )
            all_education_facts = [
                fact for fact in facts
                if fact.fact_type == CandidateFactType.EDUCATION_LEVEL.value
            ]
            if any(
                fact.status == CandidateFactStatus.EXTRACTED.value
                for fact in all_education_facts
            ):
                return (
                    ScreeningOutcome.UNKNOWN,
                    "Education evidence has not been human-reviewed.",
                    [],
                )
            education = [
                fact for fact in reviewed_facts
                if fact.fact_type == CandidateFactType.EDUCATION_LEVEL.value
            ]
            if any(
                fact.polarity != CandidateFactPolarity.AFFIRMED.value for fact in education
            ):
                return ScreeningOutcome.UNKNOWN, "Education evidence is uncertain.", []
            degree_values = {fact.value.strip().upper() for fact in education}
            if len(degree_values) != 1:
                return (
                    ScreeningOutcome.UNKNOWN,
                    "Education evidence is missing, unrecognized, or conflicting.",
                    [],
                )
            fact = education[0]
            evidence_ids = cls._fact_evidence_ids(
                uow, tenant_id, [fact], document_version_id
            )
            if not evidence_ids:
                return ScreeningOutcome.UNKNOWN, "Education fact has no source evidence.", []
            minimum_degree = str(config.get("minimum_degree", "")).upper()
            actual_label = fact.value.strip().upper()
            if actual_label == minimum_degree:
                return (
                    ScreeningOutcome.PASS,
                    f"Cited degree '{fact.value}' matches the minimum '{minimum_degree}'.",
                    [fact],
                )
            actual = _DEGREE_ORDER.get(actual_label)
            minimum_degree_rank = _DEGREE_ORDER.get(minimum_degree)
            if actual is None or minimum_degree_rank is None:
                return ScreeningOutcome.UNKNOWN, "Education criterion is not comparable.", []
            if actual >= minimum_degree_rank:
                return (
                    ScreeningOutcome.PASS,
                    f"Cited degree '{fact.value}' meets the minimum "
                    f"'{config.get('minimum_degree')}'.",
                    [fact],
                )
            return (
                ScreeningOutcome.FAIL,
                f"Cited degree '{fact.value}' is below the minimum "
                f"'{config.get('minimum_degree')}'.",
                [fact],
            )

        if criterion_type == CriterionType.CERTIFICATION_PRESENT.value:
            if (
                config.get("issuing_organization")
                or config.get("max_age_months")
                or not config.get("allow_expired", False)
            ):
                return (
                    ScreeningOutcome.UNKNOWN,
                    "Certification issuer or current validity is not represented by the "
                    "available facts.",
                    [],
                )
            target = str(config.get("certification_name", "")).strip().casefold()
            matches = [
                fact
                for fact in facts
                if fact.fact_type == CandidateFactType.CERTIFICATION.value
                and fact.value.strip().casefold() == target
            ]
            if any(fact.status == CandidateFactStatus.EXTRACTED.value for fact in matches):
                return (
                    ScreeningOutcome.UNKNOWN,
                    "Certification evidence has not been human-reviewed.",
                    [],
                )
            used = [
                fact
                for fact in reviewed_facts
                if fact.fact_type == CandidateFactType.CERTIFICATION.value
                and fact.value.strip().casefold() == target
            ]
            polarities = {fact.polarity for fact in used}
            if len(polarities) > 1 or CandidateFactPolarity.UNKNOWN.value in polarities:
                return (
                    ScreeningOutcome.UNKNOWN,
                    "Certification evidence is conflicting or uncertain.",
                    [],
                )
            if used:
                used = used[:1]
                if cls._fact_evidence_ids(uow, tenant_id, used, document_version_id):
                    if polarities == {CandidateFactPolarity.NEGATED.value}:
                        return (
                            ScreeningOutcome.FAIL,
                            "Cited human-reviewed evidence negates this certification.",
                            used,
                        )
                    return (
                        ScreeningOutcome.PASS,
                        "A cited human-reviewed fact affirms the certification.",
                        used,
                    )
                return (
                    ScreeningOutcome.UNKNOWN,
                    "Matching certification fact has no source evidence.",
                    [],
                )
            return (
                ScreeningOutcome.UNKNOWN,
                "No supported fact establishes this certification.",
                [],
            )

        if criterion_type == CriterionType.MANUAL_REQUIREMENT.value:
            return (
                ScreeningOutcome.UNKNOWN,
                "This requirement is reserved for human verification.",
                [],
            )

        return ScreeningOutcome.UNKNOWN, "Unsupported criterion type.", []

    @classmethod
    def run(cls, uow: UnitOfWork, actor: ActorContext, application_id: UUID) -> ScreeningRun:
        tenant_id = cls._authorize(actor)
        application = uow.session.scalar(
            select(Application).where(
                Application.tenant_id == tenant_id,
                Application.id == application_id,
            ).with_for_update()
        )
        if application is None:
            raise ProblemException(
                status_code=404,
                title="Resource Not Found",
                code="RESOURCE_NOT_FOUND",
                detail="The requested application was not found.",
            )
        job = uow.session.scalar(
            select(Job)
            .where(Job.tenant_id == tenant_id, Job.id == application.job_id)
            .with_for_update()
        )
        if job is None or job.current_published_version_id is None:
            raise ProblemException(
                status_code=409,
                title="Requirements Not Published",
                code="REQUIREMENTS_NOT_PUBLISHED",
                detail="Screening requires a published requirement version.",
            )
        requirement = uow.session.scalar(
            select(RequirementVersion).where(
                RequirementVersion.tenant_id == tenant_id,
                RequirementVersion.id == job.current_published_version_id,
            )
        )
        if requirement is None:
            raise ProblemException(
                status_code=409,
                title="Requirements Not Published",
                code="REQUIREMENTS_NOT_PUBLISHED",
                detail="The current published requirement version is unavailable.",
            )
        criteria = list(
            uow.session.scalars(
                select(Criterion)
                .where(
                    Criterion.tenant_id == tenant_id,
                    Criterion.requirement_version_id == requirement.id,
                )
                .order_by(Criterion.order_index.asc())
            ).all()
        )
        active_binding = uow.session.scalar(
            select(ApplicationDocument).where(
                ApplicationDocument.tenant_id == tenant_id,
                ApplicationDocument.application_id == application.id,
                ApplicationDocument.status == ApplicationDocumentStatus.ACTIVE.value,
            )
        )
        document_version_id = (
            active_binding.document_version_id if active_binding is not None else None
        )
        facts = cls._latest_facts(
            uow, tenant_id, application.candidate_id, document_version_id
        )
        evaluated = [
            (
                criterion,
                *cls._evaluate(uow, tenant_id, criterion, facts, document_version_id),
            )
            for criterion in criteria
        ]
        required_outcomes = [
            outcome for criterion, outcome, _, _ in evaluated if criterion.is_required
        ]
        if not required_outcomes or ScreeningOutcome.FAIL in required_outcomes:
            outcome = (
                ScreeningOutcome.FAIL
                if ScreeningOutcome.FAIL in required_outcomes
                else ScreeningOutcome.UNKNOWN
            )
        elif ScreeningOutcome.UNKNOWN in required_outcomes:
            outcome = ScreeningOutcome.UNKNOWN
        else:
            outcome = ScreeningOutcome.PASS

        run = ScreeningRun(
            id=uuid4(),
            tenant_id=tenant_id,
            application_id=application.id,
            candidate_id=application.candidate_id,
            application_document_id=active_binding.id if active_binding else None,
            document_version_id=document_version_id,
            requirement_version_id=requirement.id,
            outcome=outcome.value,
            rule_version=_RULE_VERSION,
            created_by=actor.user_id,
        )
        uow.session.add(run)
        uow.session.flush()
        for criterion, result, explanation, used_facts in evaluated:
            result_row = ScreeningResult(
                id=uuid4(),
                tenant_id=tenant_id,
                screening_run_id=run.id,
                criterion_id=criterion.id,
                criterion_type=criterion.criterion_type,
                is_required=criterion.is_required,
                outcome=result.value,
                explanation=explanation,
            )
            uow.session.add(result_row)
            uow.session.flush()
            for fact in used_facts:
                uow.session.add(
                    ScreeningResultFact(
                        id=uuid4(),
                        tenant_id=tenant_id,
                        screening_result_id=result_row.id,
                        fact_revision_id=fact.id,
                    )
                )
        uow.session.flush()
        uow.record_audit(
            resource_type="screening_run",
            action="SCREENING_COMPLETED",
            resource_id=run.id,
            actor_id=actor.user_id,
            details={
                "application_id": str(application.id),
                "requirement_version_id": str(requirement.id),
                "outcome": run.outcome,
                "rule_version": run.rule_version,
            },
        )
        return run

    @classmethod
    def get_run(
        cls, uow: UnitOfWork, actor: ActorContext, screening_run_id: UUID
    ) -> tuple[ScreeningRun, list[ScreeningResult], ScreeningOverride | None]:
        tenant_id = cls._authorize(actor)
        run = uow.session.scalar(
            select(ScreeningRun).where(
                ScreeningRun.tenant_id == tenant_id,
                ScreeningRun.id == screening_run_id,
            ).with_for_update()
        )
        if run is None:
            raise ProblemException(
                status_code=404,
                title="Resource Not Found",
                code="RESOURCE_NOT_FOUND",
                detail="The requested screening run was not found.",
            )
        results = list(
            uow.session.scalars(
                select(ScreeningResult)
                .where(
                    ScreeningResult.tenant_id == tenant_id,
                    ScreeningResult.screening_run_id == run.id,
                )
                .order_by(ScreeningResult.criterion_id)
            ).all()
        )
        latest_override = uow.session.scalar(
            select(ScreeningOverride)
            .where(
                ScreeningOverride.tenant_id == tenant_id,
                ScreeningOverride.screening_run_id == run.id,
            )
            .order_by(ScreeningOverride.revision_number.desc())
            .limit(1)
        )
        return run, results, latest_override

    @classmethod
    def override(
        cls,
        uow: UnitOfWork,
        actor: ActorContext,
        screening_run_id: UUID,
        outcome: ScreeningOutcome,
        reason: str,
    ) -> ScreeningOverride:
        tenant_id = cls._authorize(actor)
        if not actor.membership or actor.membership.role != Role.RECRUITER.value:
            raise ProblemException(
                status_code=403,
                title="Forbidden",
                code="CAPABILITY_DENIED",
                detail="Only a recruiter may override a screening outcome.",
            )
        if not actor.is_human:
            raise ProblemException(
                status_code=403,
                title="Forbidden",
                code="CAPABILITY_DENIED",
                detail="Only a human recruiter may override a screening outcome.",
            )
        if not reason.strip():
            raise ProblemException(
                status_code=422,
                title="Override Reason Required",
                code="SCREENING_OVERRIDE_REASON_REQUIRED",
                detail="A recruiter override must include a reason.",
            )
        run = uow.session.scalar(
            select(ScreeningRun).where(
                ScreeningRun.tenant_id == tenant_id,
                ScreeningRun.id == screening_run_id,
            ).with_for_update()
        )
        if run is None:
            raise ProblemException(
                status_code=404,
                title="Resource Not Found",
                code="RESOURCE_NOT_FOUND",
                detail="The requested screening run was not found.",
            )
        current_revision = uow.session.scalar(
            select(ScreeningOverride.revision_number)
            .where(
                ScreeningOverride.tenant_id == tenant_id,
                ScreeningOverride.screening_run_id == run.id,
            )
            .order_by(ScreeningOverride.revision_number.desc())
            .limit(1)
        )
        override = ScreeningOverride(
            id=uuid4(),
            tenant_id=tenant_id,
            screening_run_id=run.id,
            revision_number=(current_revision or 0) + 1,
            outcome=outcome.value,
            reason=reason.strip(),
            actor_id=actor.user_id,
        )
        uow.session.add(override)
        uow.session.flush()
        uow.record_audit(
            resource_type="screening_run",
            action="SCREENING_OVERRIDDEN",
            resource_id=run.id,
            actor_id=actor.user_id,
            details={
                "override_id": str(override.id),
                "original_outcome": run.outcome,
                "override_outcome": override.outcome,
            },
        )
        return override

    @classmethod
    def list_overrides(
        cls, uow: UnitOfWork, actor: ActorContext, screening_run_id: UUID
    ) -> list[ScreeningOverride]:
        tenant_id = cls._authorize(actor)
        return list(
            uow.session.scalars(
                select(ScreeningOverride)
                .where(
                    ScreeningOverride.tenant_id == tenant_id,
                    ScreeningOverride.screening_run_id == screening_run_id,
                )
                .order_by(ScreeningOverride.revision_number.asc())
            ).all()
        )
