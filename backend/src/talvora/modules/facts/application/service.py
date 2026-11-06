"""Candidate fact derivation, review, and revision history."""

from uuid import UUID, uuid4

from sqlalchemy import select

from talvora.modules.candidates.domain.models import Candidate
from talvora.modules.documents.domain.models import Document
from talvora.modules.evidence.domain.models import (
    EXTRACTION_RUN_READABLE_STATUSES,
    EvidenceFragment,
    ExtractionRun,
    SourceSegment,
)
from talvora.modules.facts.domain.models import (
    CandidateFactEvidence,
    CandidateFactPolarity,
    CandidateFactRevision,
    CandidateFactStatus,
    CandidateFactType,
)
from talvora.modules.facts.domain.validation import is_evidence_supported
from talvora.modules.identity.domain.roles import Capability, Role
from talvora.shared.application.actor import ActorContext
from talvora.shared.infrastructure.uow import UnitOfWork
from talvora.shared.transport.problems import ProblemException


class CandidateFactService:
    """Creates evidence-derived facts and immutable human-reviewed revisions."""

    @staticmethod
    def _tenant(actor: ActorContext) -> UUID:
        if not actor.is_authenticated or actor.tenant_id is None or actor.user_id is None:
            raise ProblemException(
                status_code=401,
                title="Unauthorized",
                code="UNAUTHENTICATED",
                detail="An authenticated organization member is required.",
            )
        return actor.tenant_id

    @staticmethod
    def _require(actor: ActorContext, capability: Capability) -> None:
        if not actor.has_capability(capability.value):
            raise ProblemException(
                status_code=403,
                title="Forbidden",
                code="CAPABILITY_DENIED",
                detail=f"Actor lacks capability '{capability.value}'.",
            )

    @classmethod
    def _candidate(
        cls, uow: UnitOfWork, tenant_id: UUID, candidate_id: UUID, for_update: bool = False
    ) -> Candidate:
        statement = select(Candidate).where(
            Candidate.tenant_id == tenant_id, Candidate.id == candidate_id
        )
        if for_update:
            statement = statement.with_for_update()
        candidate = uow.session.scalar(statement)
        if candidate is None:
            raise ProblemException(
                status_code=404,
                title="Resource Not Found",
                code="RESOURCE_NOT_FOUND",
                detail="The requested candidate was not found.",
            )
        return candidate

    @classmethod
    def _latest_revision(
        cls, uow: UnitOfWork, tenant_id: UUID, candidate_id: UUID, fact_key: UUID
    ) -> CandidateFactRevision:
        revision = uow.session.scalar(
            select(CandidateFactRevision)
            .where(
                CandidateFactRevision.tenant_id == tenant_id,
                CandidateFactRevision.candidate_id == candidate_id,
                CandidateFactRevision.fact_key == fact_key,
            )
            .order_by(CandidateFactRevision.revision_number.desc())
            .limit(1)
        )
        if revision is None:
            raise ProblemException(
                status_code=404,
                title="Resource Not Found",
                code="RESOURCE_NOT_FOUND",
                detail="The requested candidate fact was not found.",
            )
        return revision

    @classmethod
    def _evidence_for_candidate(
        cls, uow: UnitOfWork, tenant_id: UUID, candidate_id: UUID, evidence_id: UUID
    ) -> tuple[EvidenceFragment, ExtractionRun, SourceSegment]:
        row = uow.session.execute(
            select(EvidenceFragment, ExtractionRun, SourceSegment)
            .join(
                ExtractionRun,
                (ExtractionRun.tenant_id == EvidenceFragment.tenant_id)
                & (ExtractionRun.id == EvidenceFragment.extraction_run_id),
            )
            .join(
                SourceSegment,
                (SourceSegment.tenant_id == EvidenceFragment.tenant_id)
                & (SourceSegment.id == EvidenceFragment.source_segment_id),
            )
            .join(
                Document,
                (Document.tenant_id == EvidenceFragment.tenant_id)
                & (Document.id == ExtractionRun.document_id),
            )
            .where(
                EvidenceFragment.tenant_id == tenant_id,
                EvidenceFragment.id == evidence_id,
                EvidenceFragment.document_version_id == ExtractionRun.document_version_id,
                EvidenceFragment.document_version_id == SourceSegment.document_version_id,
                Document.candidate_id == candidate_id,
                ExtractionRun.status.in_(EXTRACTION_RUN_READABLE_STATUSES),
            )
        ).one_or_none()
        if row is None:
            raise ProblemException(
                status_code=422,
                title="Invalid Evidence Reference",
                code="FACT_EVIDENCE_INVALID",
                detail="Evidence must belong to this candidate and a readable extraction run.",
            )
        return row[0], row[1], row[2]

    @classmethod
    def list_facts(
        cls, uow: UnitOfWork, actor: ActorContext, candidate_id: UUID
    ) -> list[tuple[CandidateFactRevision, list[UUID]]]:
        tenant_id = cls._tenant(actor)
        cls._require(actor, Capability.READ_DOCUMENTS)
        if actor.membership and actor.membership.role == Role.INTERVIEWER.value:
            raise ProblemException(
                status_code=403,
                title="Forbidden",
                code="CAPABILITY_DENIED",
                detail="Interviewers may not access candidate facts or source excerpts.",
            )
        cls._candidate(uow, tenant_id, candidate_id)
        revisions = uow.session.scalars(
            select(CandidateFactRevision)
            .where(
                CandidateFactRevision.tenant_id == tenant_id,
                CandidateFactRevision.candidate_id == candidate_id,
            )
            .order_by(
                CandidateFactRevision.fact_key,
                CandidateFactRevision.revision_number.desc(),
            )
        ).all()
        latest: dict[UUID, CandidateFactRevision] = {}
        for revision in revisions:
            latest.setdefault(revision.fact_key, revision)
        result: list[tuple[CandidateFactRevision, list[UUID]]] = []
        for revision in latest.values():
            evidence_ids = list(
                uow.session.scalars(
                    select(CandidateFactEvidence.evidence_fragment_id).where(
                        CandidateFactEvidence.tenant_id == tenant_id,
                        CandidateFactEvidence.fact_revision_id == revision.id,
                    )
                ).all()
            )
            result.append((revision, evidence_ids))
        return sorted(result, key=lambda item: (item[0].fact_type, item[0].value.casefold()))

    @classmethod
    def list_revisions(
        cls, uow: UnitOfWork, actor: ActorContext, candidate_id: UUID, fact_key: UUID
    ) -> list[CandidateFactRevision]:
        tenant_id = cls._tenant(actor)
        cls._require(actor, Capability.READ_DOCUMENTS)
        if actor.membership and actor.membership.role == Role.INTERVIEWER.value:
            raise ProblemException(
                status_code=403,
                title="Forbidden",
                code="CAPABILITY_DENIED",
                detail="Interviewers may not access candidate facts or source excerpts.",
            )
        cls._candidate(uow, tenant_id, candidate_id)
        cls._latest_revision(uow, tenant_id, candidate_id, fact_key)
        return list(
            uow.session.scalars(
                select(CandidateFactRevision)
                .where(
                    CandidateFactRevision.tenant_id == tenant_id,
                    CandidateFactRevision.candidate_id == candidate_id,
                    CandidateFactRevision.fact_key == fact_key,
                )
                .order_by(CandidateFactRevision.revision_number.asc())
            ).all()
        )

    @classmethod
    def derive_fact(
        cls,
        uow: UnitOfWork,
        actor: ActorContext,
        candidate_id: UUID,
        fact_type: CandidateFactType,
        value: str,
        polarity: CandidateFactPolarity,
        evidence_fragment_ids: list[UUID],
    ) -> CandidateFactRevision:
        tenant_id = cls._tenant(actor)
        cls._require(actor, Capability.EDIT_CANDIDATE)
        if not actor.is_human:
            raise ProblemException(
                status_code=403,
                title="Forbidden",
                code="CAPABILITY_DENIED",
                detail="Only a human reviewer may derive a candidate fact.",
            )
        cls._candidate(uow, tenant_id, candidate_id)
        clean_value = value.strip()
        if not clean_value or not evidence_fragment_ids:
            raise ProblemException(
                status_code=422,
                title="Invalid Candidate Fact",
                code="FACT_VALUE_UNSUPPORTED",
                detail="A fact needs a non-empty value and at least one source excerpt.",
            )

        evidences: list[EvidenceFragment] = []
        runs: list[ExtractionRun] = []
        for evidence_id in dict.fromkeys(evidence_fragment_ids):
            fragment, run, segment = cls._evidence_for_candidate(
                uow, tenant_id, candidate_id, evidence_id
            )
            if segment.is_low_confidence:
                raise ProblemException(
                    status_code=422,
                    title="Insufficient Evidence",
                    code="FACT_EVIDENCE_LOW_CONFIDENCE",
                    detail="Low-confidence source text cannot support a structured fact.",
                )
            if not is_evidence_supported(clean_value, fragment.quote):
                raise ProblemException(
                    status_code=422,
                    title="Unsupported Candidate Fact",
                    code="FACT_VALUE_UNSUPPORTED",
                    detail="Extracted fact value must appear in its cited source excerpt.",
                )
            evidences.append(fragment)
            runs.append(run)

        if len({run.id for run in runs}) != 1:
            raise ProblemException(
                status_code=422,
                title="Invalid Evidence References",
                code="FACT_EVIDENCE_MIXED_RUNS",
                detail="A derived fact must cite evidence from one extraction revision.",
            )

        fact = CandidateFactRevision(
            id=uuid4(),
            tenant_id=tenant_id,
            candidate_id=candidate_id,
            fact_key=uuid4(),
            revision_number=1,
            fact_type=fact_type.value,
            value=clean_value,
            extracted_value=clean_value,
            polarity=polarity.value,
            extracted_polarity=polarity.value,
            status=CandidateFactStatus.EXTRACTED.value,
            extraction_run_id=runs[0].id,
            created_by=actor.user_id,
        )
        uow.session.add(fact)
        uow.session.flush()
        for fragment in evidences:
            uow.session.add(
                CandidateFactEvidence(
                    id=uuid4(),
                    tenant_id=tenant_id,
                    fact_revision_id=fact.id,
                    evidence_fragment_id=fragment.id,
                )
            )
        uow.record_audit(
            resource_type="candidate_fact",
            action="FACT_DERIVED",
            resource_id=fact.id,
            actor_id=actor.user_id,
            actor_capability=Capability.EDIT_CANDIDATE.value,
            details={"candidate_id": str(candidate_id), "fact_type": fact.fact_type},
        )
        return fact

    @classmethod
    def review_fact(
        cls,
        uow: UnitOfWork,
        actor: ActorContext,
        candidate_id: UUID,
        fact_key: UUID,
        expected_revision: int,
        status: CandidateFactStatus,
        corrected_value: str | None,
        polarity: CandidateFactPolarity | None,
        review_note: str,
    ) -> CandidateFactRevision:
        tenant_id = cls._tenant(actor)
        cls._require(actor, Capability.EDIT_CANDIDATE)
        if not actor.is_human:
            raise ProblemException(
                status_code=403,
                title="Forbidden",
                code="CAPABILITY_DENIED",
                detail="Only a human reviewer may verify, correct, or reject a candidate fact.",
            )
        cls._candidate(uow, tenant_id, candidate_id, for_update=True)
        current = cls._latest_revision(uow, tenant_id, candidate_id, fact_key)
        if current.revision_number != expected_revision:
            raise ProblemException(
                status_code=409,
                title="Fact Revision Conflict",
                code="VERSION_CONFLICT",
                detail="The candidate fact changed; reload its latest revision before reviewing.",
            )
        if current.status == CandidateFactStatus.REJECTED.value:
            raise ProblemException(
                status_code=409,
                title="Fact Already Rejected",
                code="FACT_REJECTED",
                detail="A rejected fact cannot be revised; derive a new fact from evidence.",
            )
        note = review_note.strip()
        if status in (CandidateFactStatus.CORRECTED, CandidateFactStatus.REJECTED) and not note:
            raise ProblemException(
                status_code=422,
                title="Review Reason Required",
                code="FACT_REVIEW_REASON_REQUIRED",
                detail="Corrections and rejections require a human review reason.",
            )
        if status == CandidateFactStatus.EXTRACTED:
            raise ProblemException(
                status_code=422,
                title="Invalid Review Status",
                code="FACT_REVIEW_STATUS_INVALID",
                detail="Review must verify, correct, or reject an extracted fact.",
            )
        if status != CandidateFactStatus.CORRECTED and corrected_value is not None:
            raise ProblemException(
                status_code=422,
                title="Unexpected Corrected Value",
                code="FACT_VALUE_INVALID",
                detail="A corrected value is only valid for a CORRECTED review.",
            )
        if status != CandidateFactStatus.CORRECTED and polarity is not None:
            raise ProblemException(
                status_code=422,
                title="Unexpected Corrected Polarity",
                code="FACT_POLARITY_INVALID",
                detail="A polarity change is only valid for a CORRECTED review.",
            )
        value = current.value if status != CandidateFactStatus.CORRECTED else (
            corrected_value.strip() if corrected_value else ""
        )
        if not value:
            raise ProblemException(
                status_code=422,
                title="Corrected Value Required",
                code="FACT_VALUE_REQUIRED",
                detail="A correction must provide a non-empty value.",
            )
        revised = CandidateFactRevision(
            id=uuid4(),
            tenant_id=tenant_id,
            candidate_id=candidate_id,
            fact_key=fact_key,
            revision_number=current.revision_number + 1,
            fact_type=current.fact_type,
            value=value,
            extracted_value=current.extracted_value,
            polarity=(polarity.value if polarity is not None else current.polarity),
            extracted_polarity=current.extracted_polarity,
            status=status.value,
            review_note=note or None,
            extraction_run_id=current.extraction_run_id,
            created_by=actor.user_id,
        )
        uow.session.add(revised)
        uow.session.flush()
        evidence_ids = uow.session.scalars(
            select(CandidateFactEvidence.evidence_fragment_id).where(
                CandidateFactEvidence.tenant_id == tenant_id,
                CandidateFactEvidence.fact_revision_id == current.id,
            )
        ).all()
        for evidence_id in evidence_ids:
            uow.session.add(
                CandidateFactEvidence(
                    id=uuid4(),
                    tenant_id=tenant_id,
                    fact_revision_id=revised.id,
                    evidence_fragment_id=evidence_id,
                )
            )
        uow.record_audit(
            resource_type="candidate_fact",
            action="FACT_REVIEWED",
            resource_id=revised.id,
            actor_id=actor.user_id,
            actor_capability=Capability.EDIT_CANDIDATE.value,
            details={
                "candidate_id": str(candidate_id),
                "fact_key": str(fact_key),
                "revision_number": revised.revision_number,
                "status": revised.status,
            },
        )
        return revised
