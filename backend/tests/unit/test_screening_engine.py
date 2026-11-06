"""Unit tests for deterministic tri-state screening rules."""

from typing import Any, cast
from unittest.mock import Mock
from uuid import UUID, uuid4

import pytest

from talvora.modules.facts.domain.models import (
    CandidateFactPolarity,
    CandidateFactRevision,
    CandidateFactStatus,
)
from talvora.modules.jobs.domain.models import Criterion, CriterionType
from talvora.modules.screening.application.service import ScreeningService
from talvora.modules.screening.domain.models import ScreeningOutcome
from talvora.shared.infrastructure.uow import UnitOfWork


def _criterion(criterion_type: CriterionType, config: dict[str, Any]) -> Criterion:
    return Criterion(
        id=uuid4(),
        tenant_id=uuid4(),
        criterion_type=criterion_type.value,
        configuration=config,
        is_required=True,
        requires_human_review=False,
    )


def _fact(fact_type: str, value: str, fact_key: UUID | None = None) -> CandidateFactRevision:
    return CandidateFactRevision(
        id=uuid4(),
        tenant_id=uuid4(),
        candidate_id=uuid4(),
        fact_key=fact_key or uuid4(),
        revision_number=1,
        fact_type=fact_type,
        value=value,
        extracted_value=value,
        polarity=CandidateFactPolarity.AFFIRMED.value,
        extracted_polarity=CandidateFactPolarity.AFFIRMED.value,
        status=CandidateFactStatus.VERIFIED.value,
        extraction_run_id=uuid4(),
        created_by=uuid4(),
    )


def _uow() -> UnitOfWork:
    return cast(UnitOfWork, Mock())


def test_missing_skill_evidence_is_unknown() -> None:
    criterion = _criterion(
        CriterionType.SKILL_PRESENT,
        {"criterion_type": "SKILL_PRESENT", "skill_name": "Python"},
    )

    outcome, explanation, used_facts = ScreeningService._evaluate(
        _uow(), uuid4(), criterion, [], uuid4()
    )

    assert outcome == ScreeningOutcome.UNKNOWN
    assert "No human-reviewed fact" in explanation
    assert used_facts == []


def test_missing_application_binding_is_unknown() -> None:
    criterion = _criterion(
        CriterionType.SKILL_PRESENT,
        {"criterion_type": "SKILL_PRESENT", "skill_name": "Python"},
    )
    fact = _fact("SKILL", "Python")

    outcome, explanation, used_facts = ScreeningService._evaluate(
        _uow(), uuid4(), criterion, [fact], None
    )

    assert outcome == ScreeningOutcome.UNKNOWN
    assert "No active application document" in explanation
    assert used_facts == []


def test_insufficient_experience_is_fail_when_fact_is_unambiguous(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    evidence_id = uuid4()
    monkeypatch.setattr(
        ScreeningService,
        "_fact_evidence_ids",
        staticmethod(lambda _uow, _tenant, _facts, _document_version_id: [evidence_id]),
    )
    criterion = _criterion(
        CriterionType.MIN_EXPERIENCE_MONTHS,
        {"criterion_type": "MIN_EXPERIENCE_MONTHS", "min_months": 36},
    )
    fact = _fact("EXPERIENCE_MONTHS", "24 months")

    outcome, explanation, used_facts = ScreeningService._evaluate(
        _uow(), uuid4(), criterion, [fact], uuid4()
    )

    assert outcome == ScreeningOutcome.FAIL
    assert "24 months; 36 required" in explanation
    assert used_facts == [fact]


def test_conflicting_experience_evidence_is_unknown() -> None:
    criterion = _criterion(
        CriterionType.MIN_EXPERIENCE_MONTHS,
        {"criterion_type": "MIN_EXPERIENCE_MONTHS", "min_months": 36},
    )
    facts = [_fact("EXPERIENCE_MONTHS", "24 months"), _fact("EXPERIENCE_MONTHS", "48 months")]

    outcome, explanation, used_facts = ScreeningService._evaluate(
        _uow(), uuid4(), criterion, facts, uuid4()
    )

    assert outcome == ScreeningOutcome.UNKNOWN
    assert "conflicting" in explanation
    assert used_facts == []


def test_matching_skill_passes_only_with_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    evidence_id = uuid4()
    monkeypatch.setattr(
        ScreeningService,
        "_fact_evidence_ids",
        staticmethod(lambda _uow, _tenant, _facts, _document_version_id: [evidence_id]),
    )
    criterion = _criterion(
        CriterionType.SKILL_PRESENT,
        {
            "criterion_type": "SKILL_PRESENT",
            "skill_name": "Python",
            "canonical_name": "Python",
            "synonyms": ["Python 3"],
        },
    )
    fact = _fact("SKILL", "python")

    outcome, explanation, used_facts = ScreeningService._evaluate(
        _uow(), uuid4(), criterion, [fact], uuid4()
    )

    assert outcome == ScreeningOutcome.PASS
    assert "cited" in explanation
    assert used_facts == [fact]


def test_skill_proficiency_constraint_without_proficiency_fact_is_unknown() -> None:
    criterion = _criterion(
        CriterionType.SKILL_PRESENT,
        {
            "criterion_type": "SKILL_PRESENT",
            "skill_name": "Python",
            "min_proficiency": "ADVANCED",
        },
    )
    fact = _fact("SKILL", "Python")

    outcome, explanation, used_facts = ScreeningService._evaluate(
        _uow(), uuid4(), criterion, [fact], uuid4()
    )

    assert outcome == ScreeningOutcome.UNKNOWN
    assert "Proficiency" in explanation
    assert used_facts == []


def test_unreviewed_matching_fact_is_unknown() -> None:
    criterion = _criterion(
        CriterionType.SKILL_PRESENT,
        {"criterion_type": "SKILL_PRESENT", "skill_name": "Python"},
    )
    fact = _fact("SKILL", "Python")
    fact.status = CandidateFactStatus.EXTRACTED.value

    outcome, explanation, used_facts = ScreeningService._evaluate(
        _uow(), uuid4(), criterion, [fact], uuid4()
    )

    assert outcome == ScreeningOutcome.UNKNOWN
    assert "not been human-reviewed" in explanation
    assert used_facts == []


def test_negated_human_reviewed_skill_is_fail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    evidence_id = uuid4()
    monkeypatch.setattr(
        ScreeningService,
        "_fact_evidence_ids",
        staticmethod(lambda _uow, _tenant, _facts, _document_version_id: [evidence_id]),
    )
    criterion = _criterion(
        CriterionType.SKILL_PRESENT,
        {"criterion_type": "SKILL_PRESENT", "skill_name": "Python"},
    )
    fact = _fact("SKILL", "Python")
    fact.polarity = CandidateFactPolarity.NEGATED.value

    outcome, explanation, used_facts = ScreeningService._evaluate(
        _uow(), uuid4(), criterion, [fact], uuid4()
    )

    assert outcome == ScreeningOutcome.FAIL
    assert "negates" in explanation
    assert used_facts == [fact]


def test_scoped_experience_cannot_be_inferred_from_unqualified_months() -> None:
    criterion = _criterion(
        CriterionType.MIN_EXPERIENCE_MONTHS,
        {
            "criterion_type": "MIN_EXPERIENCE_MONTHS",
            "min_months": 36,
            "relevant_titles": ["Backend Engineer"],
        },
    )
    fact = _fact("EXPERIENCE_MONTHS", "48 months")

    outcome, explanation, used_facts = ScreeningService._evaluate(
        _uow(), uuid4(), criterion, [fact], uuid4()
    )

    assert outcome == ScreeningOutcome.UNKNOWN
    assert "scope" in explanation
    assert used_facts == []
