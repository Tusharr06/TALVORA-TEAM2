"""Unit tests for Job domain models, criteria schemas, sanitization, and protected-trait guards."""

import pytest
from pydantic import ValidationError

from talvora.modules.jobs.domain.models import (
    CertificationPresentConfig,
    CriterionDraft,
    CriterionType,
    DegreeLevel,
    EducationLevelConfig,
    ManualRequirementConfig,
    MinExperienceMonthsConfig,
    SkillPresentConfig,
    sanitize_text,
    validate_no_protected_traits,
)
from talvora.shared.transport.problems import ProblemException


def test_sanitize_text_strips_dangerous_tags_and_scripts() -> None:
    raw = (
        "<script>alert('xss')</script>Senior Backend Engineer<iframe src='malicious.com'></iframe>"
    )
    clean = sanitize_text(raw)
    assert "<script>" not in clean
    assert "alert" not in clean
    assert "iframe" not in clean
    assert clean == "Senior Backend Engineer"


def test_sanitize_text_preserves_plain_text() -> None:
    text = "We are seeking a Senior Python Engineer with 5+ years of experience."
    assert sanitize_text(text) == text


def test_validate_no_protected_traits_rejects_age_gender_race() -> None:
    with pytest.raises(ProblemException) as exc1:
        validate_no_protected_traits("Must be under 35 years of age", "requirement")
    assert exc1.value.status_code == 422
    assert exc1.value.code == "INVALID_CRITERION_CONFIG"
    assert "age" in exc1.value.detail.lower()

    with pytest.raises(ProblemException) as exc2:
        validate_no_protected_traits(
            "Female candidates preferred for gender balance", "requirement"
        )
    assert exc2.value.status_code == 422
    assert "gender" in exc2.value.detail.lower()

    with pytest.raises(ProblemException) as exc3:
        validate_no_protected_traits("Religious adherence required", "requirement")
    assert exc3.value.status_code == 422
    assert "religious" in exc3.value.detail.lower()


def test_skill_present_config_validation() -> None:
    config = SkillPresentConfig(
        skill_name="Python",
        canonical_name="python",
        synonyms=["py", "python3"],
        min_proficiency="ADVANCED",
    )
    assert config.criterion_type == CriterionType.SKILL_PRESENT
    assert config.skill_name == "Python"


def test_min_experience_months_rejects_zero_or_negative() -> None:
    with pytest.raises(ValidationError):
        MinExperienceMonthsConfig(min_months=0, relevant_titles=["Backend Engineer"])

    with pytest.raises(ValidationError):
        MinExperienceMonthsConfig(min_months=-12, relevant_titles=["Backend Engineer"])

    valid = MinExperienceMonthsConfig(
        min_months=36,
        relevant_titles=["Software Engineer", "Backend Developer"],
        equivalence_policy={"allow_masters_as_months": 12},
    )
    assert valid.min_months == 36


def test_education_level_config_typed_and_explicit() -> None:
    config = EducationLevelConfig(
        minimum_degree=DegreeLevel.BACHELORS,
        acceptable_fields_of_study=["Computer Science", "Information Technology"],
        allow_equivalent_experience=True,
        equivalent_experience_months=48,
    )
    assert config.minimum_degree == DegreeLevel.BACHELORS
    assert config.allow_equivalent_experience is True
    assert config.equivalent_experience_months == 48


def test_certification_present_config_typed_expiry() -> None:
    config = CertificationPresentConfig(
        certification_name="AWS Certified Solutions Architect",
        issuing_organization="Amazon Web Services",
        allow_expired=False,
        max_age_months=36,
    )
    assert config.allow_expired is False
    assert config.max_age_months == 36


def test_manual_requirement_contract() -> None:
    config = ManualRequirementConfig(
        human_verification_prompt="Verify candidate has active security clearance with contracting agency.",
        category="SECURITY_CLEARANCE",
    )
    assert config.criterion_type == CriterionType.MANUAL_REQUIREMENT

    # When inside a CriterionDraft, requires_human_review MUST be True
    draft = CriterionDraft(
        criterion_type=CriterionType.MANUAL_REQUIREMENT,
        is_required=True,
        source_requirement_text="Active Secret Security Clearance",
        relevance_rationale="Client contract requires on-site cleared personnel.",
        requires_human_review=True,
        configuration=config,
    )
    assert draft.requires_human_review is True
