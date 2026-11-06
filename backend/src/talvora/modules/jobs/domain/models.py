"""Domain Models and Declarative Schemas for Jobs, Criteria, and Assignments."""

import re
from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKeyConstraint,
    Integer,
    PrimaryKeyConstraint,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

from talvora.shared.infrastructure.models import Base
from talvora.shared.transport.problems import ProblemException


def _utc_now() -> datetime:
    return datetime.now(UTC)


# ============================================================================
# Enums
# ============================================================================


class JobStatus(StrEnum):
    """Lifecycle statuses for a job vacancy."""

    DRAFT = "DRAFT"
    OPEN = "OPEN"
    PAUSED = "PAUSED"
    CLOSED = "CLOSED"


class JobRole(StrEnum):
    """Specific role assigned to a user for a given job."""

    HIRING_MANAGER = "HIRING_MANAGER"
    RECRUITER = "RECRUITER"
    INTERVIEWER = "INTERVIEWER"


class CriterionType(StrEnum):
    """Fixed supported criterion types."""

    SKILL_PRESENT = "SKILL_PRESENT"
    MIN_EXPERIENCE_MONTHS = "MIN_EXPERIENCE_MONTHS"
    EDUCATION_LEVEL = "EDUCATION_LEVEL"
    CERTIFICATION_PRESENT = "CERTIFICATION_PRESENT"
    MANUAL_REQUIREMENT = "MANUAL_REQUIREMENT"


class DegreeLevel(StrEnum):
    """Explicit, hierarchical educational attainment levels."""

    HIGH_SCHOOL = "HIGH_SCHOOL"
    ASSOCIATES = "ASSOCIATES"
    BACHELORS = "BACHELORS"
    MASTERS = "MASTERS"
    DOCTORATE = "DOCTORATE"
    PROFESSIONAL = "PROFESSIONAL"


# ============================================================================
# Content Sanitization and Protected Trait Validation
# ============================================================================

_DANGEROUS_BLOCKS_RE = re.compile(
    r"<(script|style|iframe|object|embed|applet)[^>]*>.*?</\1>",
    re.IGNORECASE | re.DOTALL,
)
_DANGEROUS_SELF_CLOSING_RE = re.compile(
    r"<(script|style|iframe|object|embed|applet|link|meta)[^>]*/>",
    re.IGNORECASE | re.DOTALL,
)
_RAW_HTML_TAG_RE = re.compile(r"<\s*/?\s*([a-zA-Z0-9]+)(\s+[^>]*)?>", re.DOTALL)
_PROHIBITED_TRAITS_RE = re.compile(
    r"\b(age|gender|sex|sexual\s+orientation|race(?![\s-]*condition)|racial|ethnicity|religion|religious|"
    r"marital\s+status|pregnancy|pregnant|caste|disability|disabled|nationality|national\s+origin)\b",
    re.IGNORECASE,
)


def sanitize_text(text: str) -> str:
    """Sanitize raw text to prevent stored XSS and executable HTML tags."""
    if not text:
        return ""
    # Strip dangerous content blocks with contents first
    cleaned = _DANGEROUS_BLOCKS_RE.sub("", text)
    cleaned = _DANGEROUS_SELF_CLOSING_RE.sub("", cleaned)
    # Strip all remaining HTML tags
    sanitized = _RAW_HTML_TAG_RE.sub("", cleaned).strip()
    return sanitized


def validate_no_protected_traits(text: str, context_label: str = "criterion") -> None:
    """Verify that criteria do not screen against protected personal characteristics."""
    if not text:
        return
    match = _PROHIBITED_TRAITS_RE.search(text)
    if match:
        raise ProblemException(
            status_code=422,
            title="Invalid Criterion Configuration",
            code="INVALID_CRITERION_CONFIG",
            detail=(
                f"Disallowed personal or protected-trait screening '{match.group(0)}' "
                f"detected in {context_label}. Talvora strictly prohibits screening on protected attributes."
            ),
        )


# ============================================================================
# Discriminated Criterion Configurations
# ============================================================================


class SkillPresentConfig(BaseModel):
    """Configuration for SKILL_PRESENT criterion."""

    criterion_type: Literal[CriterionType.SKILL_PRESENT] = CriterionType.SKILL_PRESENT
    skill_name: str = Field(min_length=1, max_length=100)
    canonical_name: str | None = Field(default=None, max_length=100)
    synonyms: list[str] = Field(default_factory=list, max_length=20)
    min_proficiency: str | None = Field(default=None, max_length=50)

    model_config = ConfigDict(extra="forbid")


class MinExperienceMonthsConfig(BaseModel):
    """Configuration for MIN_EXPERIENCE_MONTHS criterion."""

    criterion_type: Literal[CriterionType.MIN_EXPERIENCE_MONTHS] = (
        CriterionType.MIN_EXPERIENCE_MONTHS
    )
    min_months: int = Field(
        gt=0, description="Minimum experience required in whole calendar months (>0)"
    )
    relevant_titles: list[str] = Field(
        default_factory=list, description="Explicit role titles in scope"
    )
    relevant_skills: list[str] = Field(default_factory=list, description="Relevant skills in scope")
    equivalence_policy: dict[str, Any] | None = Field(
        default=None,
        description="Versioned equivalence policy for substituting degrees or adjacent roles",
    )

    model_config = ConfigDict(extra="forbid")


class EducationLevelConfig(BaseModel):
    """Configuration for EDUCATION_LEVEL criterion."""

    criterion_type: Literal[CriterionType.EDUCATION_LEVEL] = CriterionType.EDUCATION_LEVEL
    minimum_degree: DegreeLevel
    acceptable_fields_of_study: list[str] = Field(default_factory=list)
    allow_equivalent_experience: bool = Field(default=False)
    equivalent_experience_months: int | None = Field(default=None, ge=0)

    model_config = ConfigDict(extra="forbid")


class CertificationPresentConfig(BaseModel):
    """Configuration for CERTIFICATION_PRESENT criterion."""

    criterion_type: Literal[CriterionType.CERTIFICATION_PRESENT] = (
        CriterionType.CERTIFICATION_PRESENT
    )
    certification_name: str = Field(min_length=1, max_length=200)
    issuing_organization: str | None = Field(default=None, max_length=200)
    allow_expired: bool = Field(default=False)
    max_age_months: int | None = Field(default=None, gt=0)

    model_config = ConfigDict(extra="forbid")


class ManualRequirementConfig(BaseModel):
    """Configuration for MANUAL_REQUIREMENT criterion requiring human verification."""

    criterion_type: Literal[CriterionType.MANUAL_REQUIREMENT] = CriterionType.MANUAL_REQUIREMENT
    human_verification_prompt: str = Field(
        min_length=5,
        max_length=1000,
        description="Explicit description of the human verification task required",
    )
    guidelines: str | None = Field(default=None, max_length=2000)
    category: str | None = Field(default=None, max_length=100)

    model_config = ConfigDict(extra="forbid")


CriterionConfigUnion = Annotated[
    SkillPresentConfig
    | MinExperienceMonthsConfig
    | EducationLevelConfig
    | CertificationPresentConfig
    | ManualRequirementConfig,
    Field(discriminator="criterion_type"),
]


class CriterionDraft(BaseModel):
    """A typed criterion item inside a draft or publication request."""

    id: UUID = Field(default_factory=uuid4)
    criterion_type: CriterionType
    is_required: bool = Field(
        default=True, description="True for mandatory qualification, False for preferred"
    )
    source_requirement_text: str = Field(min_length=1, max_length=2000)
    relevance_rationale: str = Field(min_length=1, max_length=2000)
    requires_human_review: bool = Field(
        default=False,
        description="Must be explicitly True for MANUAL_REQUIREMENT criteria",
    )
    competency_reference: str | None = Field(default=None, max_length=255)
    configuration: CriterionConfigUnion
    equivalence_rules: dict[str, Any] | None = None
    order_index: int = Field(default=0, ge=0)

    model_config = ConfigDict(extra="forbid")


# ============================================================================
# SQLAlchemy Declarative Models
# ============================================================================


class Job(Base):
    """Job vacancy aggregate root with tenancy, assignments, and requirement versions."""

    __tablename__ = "job"

    id: Mapped[UUID] = mapped_column(nullable=False, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text(), nullable=False)
    department: Mapped[str | None] = mapped_column(String(100), nullable=True)
    location: Mapped[str | None] = mapped_column(String(255), nullable=True)
    employment_type: Mapped[str] = mapped_column(String(50), nullable=False, default="FULL_TIME")
    team_id: Mapped[UUID | None] = mapped_column(nullable=True, index=True)
    creator_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    hiring_manager_id: Mapped[UUID | None] = mapped_column(nullable=True)
    status: Mapped[str] = mapped_column(String(50), nullable=False, default=JobStatus.DRAFT.value)
    closed_reason: Mapped[str | None] = mapped_column(Text(), nullable=True)
    current_published_version_id: Mapped[UUID | None] = mapped_column(nullable=True)
    current_published_version_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    draft_requirements: Mapped[list[dict[str, Any]] | None] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"),
        nullable=True,
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=_utc_now,
        onupdate=_utc_now,
    )

    __table_args__ = (
        PrimaryKeyConstraint("tenant_id", "id", name="pk_job"),
        UniqueConstraint("tenant_id", "id", name="uq_job_tenant_id"),
        ForeignKeyConstraint(
            ["tenant_id"],
            ["organizations.id"],
            name="fk_job_tenant",
            ondelete="CASCADE",
        ),
    )

    # Relationships
    assignments: Mapped[list["JobAssignment"]] = relationship(
        "JobAssignment",
        back_populates="job",
        cascade="all, delete-orphan",
    )
    requirement_versions: Mapped[list["RequirementVersion"]] = relationship(
        "RequirementVersion",
        back_populates="job",
        cascade="all, delete-orphan",
        order_by="RequirementVersion.version_number.desc()",
    )


class JobAssignment(Base):
    """Object-level assignment linking a member to a specific vacancy role."""

    __tablename__ = "job_assignment"

    id: Mapped[UUID] = mapped_column(nullable=False, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    job_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    user_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    role: Mapped[str] = mapped_column(String(50), nullable=False)
    assigned_by: Mapped[UUID] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utc_now
    )

    __table_args__ = (
        PrimaryKeyConstraint("tenant_id", "id", name="pk_job_assignment"),
        UniqueConstraint("tenant_id", "job_id", "user_id", "role", name="uq_job_assignment_unique"),
        ForeignKeyConstraint(
            ["tenant_id"],
            ["organizations.id"],
            name="fk_job_assignment_tenant",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "job_id"],
            ["job.tenant_id", "job.id"],
            name="fk_job_assignment_job",
            ondelete="CASCADE",
        ),
    )

    job: Mapped["Job"] = relationship("Job", back_populates="assignments")


class RequirementVersion(Base):
    """Immutable published requirement snapshot for a job."""

    __tablename__ = "requirement_version"

    id: Mapped[UUID] = mapped_column(nullable=False, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    job_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text(), nullable=False)
    published_by: Mapped[UUID] = mapped_column(nullable=False)
    published_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utc_now
    )
    change_summary: Mapped[str | None] = mapped_column(Text(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utc_now
    )

    __table_args__ = (
        PrimaryKeyConstraint("tenant_id", "id", name="pk_requirement_version"),
        UniqueConstraint("tenant_id", "id", name="uq_requirement_version_tenant_id"),
        UniqueConstraint(
            "tenant_id", "job_id", "version_number", name="uq_requirement_version_job_ver"
        ),
        ForeignKeyConstraint(
            ["tenant_id"],
            ["organizations.id"],
            name="fk_requirement_version_tenant",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "job_id"],
            ["job.tenant_id", "job.id"],
            name="fk_requirement_version_job",
            ondelete="CASCADE",
        ),
    )

    job: Mapped["Job"] = relationship("Job", back_populates="requirement_versions")
    criteria: Mapped[list["Criterion"]] = relationship(
        "Criterion",
        back_populates="requirement_version",
        cascade="all, delete-orphan",
        order_by="Criterion.order_index.asc()",
    )


class Criterion(Base):
    """An individual eligibility criterion within an immutable requirement version."""

    __tablename__ = "criterion"

    id: Mapped[UUID] = mapped_column(nullable=False, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    requirement_version_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    criterion_type: Mapped[str] = mapped_column(String(50), nullable=False)
    is_required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    source_requirement_text: Mapped[str] = mapped_column(Text(), nullable=False)
    relevance_rationale: Mapped[str] = mapped_column(Text(), nullable=False)
    requires_human_review: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    competency_reference: Mapped[str | None] = mapped_column(String(255), nullable=True)
    configuration: Mapped[dict[str, Any]] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"),
        nullable=False,
    )
    equivalence_rules: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"),
        nullable=True,
    )
    order_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utc_now
    )

    __table_args__ = (
        PrimaryKeyConstraint("tenant_id", "id", name="pk_criterion"),
        ForeignKeyConstraint(
            ["tenant_id"],
            ["organizations.id"],
            name="fk_criterion_tenant",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "requirement_version_id"],
            ["requirement_version.tenant_id", "requirement_version.id"],
            name="fk_criterion_requirement_version",
            ondelete="CASCADE",
        ),
    )

    requirement_version: Mapped["RequirementVersion"] = relationship(
        "RequirementVersion", back_populates="criteria"
    )
