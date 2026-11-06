"""Post-screening, evidence-cited summary generation and quality validation."""

from uuid import UUID, uuid4

from sqlalchemy import select

from talvora.modules.applications.domain.models import Application
from talvora.modules.documents.domain.models import (
    ApplicationDocument,
    ApplicationDocumentStatus,
    Document,
)
from talvora.modules.evidence.domain.models import (
    EXTRACTION_RUN_READABLE_STATUSES,
    EvidenceFragment,
    ExtractionRun,
    SourceSegment,
)
from talvora.modules.facts.domain.models import (
    CandidateFactEvidence,
    CandidateFactRevision,
    CandidateFactStatus,
)
from talvora.modules.facts.domain.validation import is_evidence_supported
from talvora.modules.identity.domain.roles import Capability
from talvora.modules.jobs.domain.models import Job
from talvora.modules.screening.application.service import ScreeningService
from talvora.modules.screening.domain.models import ScreeningResultFact, ScreeningRun
from talvora.modules.summaries.domain.models import (
    CandidateSummaryCitation,
    CandidateSummaryClaim,
    CandidateSummaryRevision,
)
from talvora.shared.application.actor import ActorContext
from talvora.shared.infrastructure.uow import UnitOfWork
from talvora.shared.transport.problems import ProblemException


class SummaryService:
    """Generates summaries only from cited facts after deterministic screening."""

    @staticmethod
    def _authorize(actor: ActorContext) -> UUID:
        if not actor.has_capability(Capability.READ_DOCUMENTS.value):
            raise ProblemException(
                status_code=403,
                title="Forbidden",
                code="CAPABILITY_DENIED",
                detail="Summary generation requires document review access.",
            )
        return ScreeningService._authorize(actor)

    @staticmethod
    def _current_fact(
        uow: UnitOfWork, tenant_id: UUID, candidate_id: UUID, fact_id: UUID
    ) -> CandidateFactRevision | None:
        fact = uow.session.scalar(
            select(CandidateFactRevision).where(
                CandidateFactRevision.tenant_id == tenant_id,
                CandidateFactRevision.candidate_id == candidate_id,
                CandidateFactRevision.id == fact_id,
            )
        )
        if fact is None or fact.status == CandidateFactStatus.REJECTED.value:
            return None
        latest_id = uow.session.scalar(
            select(CandidateFactRevision.id)
            .where(
                CandidateFactRevision.tenant_id == tenant_id,
                CandidateFactRevision.candidate_id == candidate_id,
                CandidateFactRevision.fact_key == fact.fact_key,
            )
            .order_by(CandidateFactRevision.revision_number.desc())
            .limit(1)
        )
        return fact if fact.id == latest_id else None

    @staticmethod
    def _evidence(
        uow: UnitOfWork,
        tenant_id: UUID,
        candidate_id: UUID,
        document_version_id: UUID | None,
        fact_id: UUID,
        evidence_id: UUID,
    ) -> tuple[EvidenceFragment, SourceSegment] | None:
        if document_version_id is None:
            return None
        row = uow.session.execute(
            select(EvidenceFragment, SourceSegment)
            .join(
                SourceSegment,
                (SourceSegment.tenant_id == EvidenceFragment.tenant_id)
                & (SourceSegment.id == EvidenceFragment.source_segment_id),
            )
            .join(
                ExtractionRun,
                (ExtractionRun.tenant_id == EvidenceFragment.tenant_id)
                & (ExtractionRun.id == EvidenceFragment.extraction_run_id),
            )
            .join(
                Document,
                (Document.tenant_id == ExtractionRun.tenant_id)
                & (Document.id == ExtractionRun.document_id),
            )
            .join(
                CandidateFactEvidence,
                (CandidateFactEvidence.tenant_id == EvidenceFragment.tenant_id)
                & (CandidateFactEvidence.evidence_fragment_id == EvidenceFragment.id),
            )
            .where(
                EvidenceFragment.tenant_id == tenant_id,
                EvidenceFragment.id == evidence_id,
                EvidenceFragment.document_version_id == document_version_id,
                EvidenceFragment.document_version_id == ExtractionRun.document_version_id,
                EvidenceFragment.document_version_id == SourceSegment.document_version_id,
                CandidateFactEvidence.fact_revision_id == fact_id,
                Document.candidate_id == candidate_id,
                ExtractionRun.status.in_(EXTRACTION_RUN_READABLE_STATUSES),
                SourceSegment.is_low_confidence.is_(False),
            )
        ).one_or_none()
        if row is None:
            return None
        return row[0], row[1]

    @classmethod
    def generate(
        cls,
        uow: UnitOfWork,
        actor: ActorContext,
        application_id: UUID,
        screening_run_id: UUID,
        draft_claims: list[tuple[str, UUID, list[UUID]]] | None = None,
    ) -> CandidateSummaryRevision:
        tenant_id = cls._authorize(actor)
        uow.session.scalar(
            select(ScreeningRun)
            .where(
                ScreeningRun.tenant_id == tenant_id,
                ScreeningRun.id == screening_run_id,
            )
            .with_for_update()
        )
        run, results, latest_override = ScreeningService.get_run(uow, actor, screening_run_id)
        application = uow.session.scalar(
            select(Application)
            .where(
                Application.tenant_id == tenant_id,
                Application.id == application_id,
                Application.id == run.application_id,
            )
            .with_for_update()
        )
        if application is None or application.candidate_id != run.candidate_id:
            raise ProblemException(
                status_code=404,
                title="Resource Not Found",
                code="RESOURCE_NOT_FOUND",
                detail="The screening run does not belong to this application.",
            )
        job = uow.session.scalar(
            select(Job)
            .where(
                Job.tenant_id == tenant_id,
                Job.id == application.job_id,
            )
            .with_for_update()
        )
        if job is None or job.current_published_version_id != run.requirement_version_id:
            raise ProblemException(
                status_code=409,
                title="Screening Inputs Stale",
                code="SCREENING_INPUT_STALE",
                detail="The job's published requirements changed after this screening run.",
            )
        current_binding = uow.session.scalar(
            select(ApplicationDocument).where(
                ApplicationDocument.tenant_id == tenant_id,
                ApplicationDocument.application_id == application.id,
                ApplicationDocument.status == ApplicationDocumentStatus.ACTIVE.value,
            )
        )
        if (current_binding.id if current_binding else None) != run.application_document_id or (
            current_binding.document_version_id if current_binding else None
        ) != run.document_version_id:
            raise ProblemException(
                status_code=409,
                title="Screening Inputs Stale",
                code="SCREENING_INPUT_STALE",
                detail="The application's active document changed after this screening run.",
            )

        if draft_claims is None:
            fact_ids = (
                set(
                    uow.session.scalars(
                        select(ScreeningResultFact.fact_revision_id).where(
                            ScreeningResultFact.tenant_id == tenant_id,
                            ScreeningResultFact.screening_result_id.in_(
                                [result.id for result in results]
                            ),
                        )
                    ).all()
                )
                if results
                else set()
            )
            draft_claims = []
            for fact_id in sorted(fact_ids, key=str):
                fact = cls._current_fact(uow, tenant_id, application.candidate_id, fact_id)
                if fact is None:
                    continue
                evidence_ids = list(
                    uow.session.scalars(
                        select(CandidateFactEvidence.evidence_fragment_id).where(
                            CandidateFactEvidence.tenant_id == tenant_id,
                            CandidateFactEvidence.fact_revision_id == fact.id,
                        )
                    ).all()
                )
                draft_claims.append((fact.value, fact.id, evidence_ids))

        quality_issues: list[str] = []
        validated: list[tuple[str, CandidateFactRevision, list[UUID]]] = []
        for index, (text, fact_id, evidence_ids) in enumerate(draft_claims):
            fact = cls._current_fact(uow, tenant_id, application.candidate_id, fact_id)
            if fact is None:
                quality_issues.append(
                    f"Claim {index + 1} references a missing, rejected, or stale fact."
                )
                continue
            if text.strip().casefold() != fact.value.strip().casefold():
                quality_issues.append(
                    f"Claim {index + 1} contains text beyond its supported fact value."
                )
                continue
            if not evidence_ids:
                quality_issues.append(f"Claim {index + 1} has no evidence citations.")
                continue
            valid_evidence: list[UUID] = []
            for evidence_id in dict.fromkeys(evidence_ids):
                evidence = cls._evidence(
                    uow,
                    tenant_id,
                    application.candidate_id,
                    run.document_version_id,
                    fact.id,
                    evidence_id,
                )
                if evidence is None:
                    continue
                fragment, _segment = evidence
                text_is_supported = is_evidence_supported(fact.value, fragment.quote)
                human_corrected = fact.status == CandidateFactStatus.CORRECTED.value and bool(
                    fact.review_note and fact.review_note.strip()
                )
                if text_is_supported or human_corrected:
                    valid_evidence.append(fragment.id)
            if not valid_evidence:
                quality_issues.append(
                    f"Claim {index + 1} is unsupported by its citations or lacks "
                    "a human correction reason."
                )
                continue
            validated.append((fact.value, fact, valid_evidence))

        revision_number = (
            uow.session.scalar(
                select(CandidateSummaryRevision.revision_number)
                .where(
                    CandidateSummaryRevision.tenant_id == tenant_id,
                    CandidateSummaryRevision.application_id == application_id,
                )
                .order_by(CandidateSummaryRevision.revision_number.desc())
                .limit(1)
            )
            or 0
        ) + 1
        accepted = not quality_issues
        screening_context = f"deterministic screening: {run.outcome}"
        if latest_override is not None:
            screening_context += f"; recruiter override: {latest_override.outcome}"
        summary_text = (
            "\n".join(
                [
                    f"Evidence summary ({screening_context}).",
                    *[f"- {fact.fact_type.lower()}: {text}" for text, fact, _ in validated],
                ]
            )
            if accepted and validated
            else (
                f"Evidence summary ({screening_context}). "
                "No evidence-backed candidate facts were available for this screening run."
                if accepted
                else ""
            )
        )
        summary = CandidateSummaryRevision(
            id=uuid4(),
            tenant_id=tenant_id,
            application_id=application_id,
            screening_run_id=run.id,
            revision_number=revision_number,
            quality_status="ACCEPTED" if accepted else "REJECTED",
            summary_text=summary_text,
            quality_issues=quality_issues,
            created_by=actor.user_id,
        )
        uow.session.add(summary)
        uow.session.flush()
        if accepted:
            for index, (text, fact, evidence_ids) in enumerate(validated):
                claim = CandidateSummaryClaim(
                    id=uuid4(),
                    tenant_id=tenant_id,
                    summary_revision_id=summary.id,
                    claim_index=index,
                    fact_revision_id=fact.id,
                    text=text,
                )
                uow.session.add(claim)
                uow.session.flush()
                for evidence_id in evidence_ids:
                    uow.session.add(
                        CandidateSummaryCitation(
                            id=uuid4(),
                            tenant_id=tenant_id,
                            summary_claim_id=claim.id,
                            evidence_fragment_id=evidence_id,
                        )
                    )
        uow.session.flush()
        uow.record_audit(
            resource_type="candidate_summary",
            action="SUMMARY_QUALITY_PASSED" if accepted else "SUMMARY_QUALITY_REJECTED",
            resource_id=summary.id,
            actor_id=actor.user_id,
            details={
                "application_id": str(application_id),
                "screening_run_id": str(run.id),
                "revision_number": revision_number,
                "quality_status": summary.quality_status,
                "issue_count": len(quality_issues),
            },
        )
        return summary

    @classmethod
    def get_summary(
        cls, uow: UnitOfWork, actor: ActorContext, summary_id: UUID
    ) -> tuple[CandidateSummaryRevision, list[tuple[CandidateSummaryClaim, list[UUID]]]]:
        tenant_id = cls._authorize(actor)
        summary = uow.session.scalar(
            select(CandidateSummaryRevision).where(
                CandidateSummaryRevision.tenant_id == tenant_id,
                CandidateSummaryRevision.id == summary_id,
            )
        )
        if summary is None:
            raise ProblemException(
                status_code=404,
                title="Resource Not Found",
                code="RESOURCE_NOT_FOUND",
                detail="The requested summary revision was not found.",
            )
        claims = list(
            uow.session.scalars(
                select(CandidateSummaryClaim)
                .where(
                    CandidateSummaryClaim.tenant_id == tenant_id,
                    CandidateSummaryClaim.summary_revision_id == summary.id,
                )
                .order_by(CandidateSummaryClaim.claim_index)
            ).all()
        )
        result = []
        for claim in claims:
            evidence_ids = list(
                uow.session.scalars(
                    select(CandidateSummaryCitation.evidence_fragment_id).where(
                        CandidateSummaryCitation.tenant_id == tenant_id,
                        CandidateSummaryCitation.summary_claim_id == claim.id,
                    )
                ).all()
            )
            result.append((claim, evidence_ids))
        return summary, result

    @classmethod
    def list_summaries(
        cls, uow: UnitOfWork, actor: ActorContext, application_id: UUID
    ) -> list[CandidateSummaryRevision]:
        tenant_id = cls._authorize(actor)
        return list(
            uow.session.scalars(
                select(CandidateSummaryRevision)
                .where(
                    CandidateSummaryRevision.tenant_id == tenant_id,
                    CandidateSummaryRevision.application_id == application_id,
                )
                .order_by(CandidateSummaryRevision.revision_number.asc())
            ).all()
        )
