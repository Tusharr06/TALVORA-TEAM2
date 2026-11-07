"""Seed realistic recruitment dataset for Talvora Intelligent Talent Operations Workspace.

Populates enterprise-grade vacancies, versioned criteria, candidates with contact revisions,
active hiring pipeline applications across all lifecycle stages, and clean document evidence
with source-addressable text extractions.
"""

from datetime import UTC, datetime, timedelta
import hashlib
import uuid

from talvora.bootstrap import create_app
from talvora.config import get_settings
from talvora.shared.infrastructure.models import Base
from talvora.modules.organizations.domain.models import Organization, Membership
from talvora.modules.identity.domain.models import User
from talvora.modules.jobs.domain.models import (
    Job,
    JobStatus,
    RequirementVersion,
    Criterion,
    CriterionType,
)
from talvora.modules.candidates.domain.models import (
    Candidate,
    CandidateSource,
    CandidateContactRevision,
    CandidateContactVerification,
    ContactSource,
    ContactVerificationSource,
)
from talvora.modules.applications.domain.models import (
    Application,
    ApplicationStage,
    ApplicationSource,
    StageEvent,
)
from talvora.modules.documents.domain.models import (
    Document,
    DocumentVersion,
    ApplicationDocument,
    DocumentVersionStorageState,
    ApplicationDocumentStatus,
)
from talvora.modules.evidence.domain.models import (
    ExtractionRun,
    SourceSegment,
    ExtractionRunStatus,
    SourceLocatorType,
    SegmentExtractionMethod,
)


def seed() -> None:
    settings = get_settings()
    app = create_app(settings)
    engine = app.state.db_manager.sync_engine
    Base.metadata.create_all(engine)
    session_maker = app.state.db_manager.session_maker

    with session_maker() as session:
        # Check or create default Organization
        org = session.query(Organization).first()
        if not org:
            org = Organization(
                name="Talvora Talent Ops",
                slug="talvora-hq",
            )
            session.add(org)
            session.flush()

        tenant_id = org.id

        # Find or create primary recruiter user
        user = session.query(User).first()
        if not user:
            user = User(
                issuer="https://cognito-idp.us-east-1.amazonaws.com/us-east-1_example",
                subject="usr_demo_recruiter_01",
                email="lead.recruiter@talvora.test",
                email_verified=True,
            )
            session.add(user)
            session.flush()

        user_id = user.id

        # Ensure active Membership
        membership = (
            session.query(Membership)
            .filter_by(tenant_id=tenant_id, user_id=user_id)
            .first()
        )
        if not membership:
            membership = Membership(
                tenant_id=tenant_id,
                user_id=user_id,
                role="ORG_ADMIN",
                status="ACTIVE",
            )
            session.add(membership)
            session.flush()

        # Clear existing data to provide a pristine realistic state
        session.query(SourceSegment).filter_by(tenant_id=tenant_id).delete()
        session.query(ExtractionRun).filter_by(tenant_id=tenant_id).delete()
        session.query(ApplicationDocument).filter_by(tenant_id=tenant_id).delete()
        session.query(DocumentVersion).filter_by(tenant_id=tenant_id).delete()
        session.query(Document).filter_by(tenant_id=tenant_id).delete()

        session.query(StageEvent).filter_by(tenant_id=tenant_id).delete()
        session.query(Application).filter_by(tenant_id=tenant_id).delete()
        session.query(CandidateContactVerification).filter_by(tenant_id=tenant_id).delete()
        session.query(CandidateContactRevision).filter_by(tenant_id=tenant_id).delete()
        session.query(Candidate).filter_by(tenant_id=tenant_id).delete()
        session.query(Criterion).filter_by(tenant_id=tenant_id).delete()
        session.query(RequirementVersion).filter_by(tenant_id=tenant_id).delete()
        session.query(Job).filter_by(tenant_id=tenant_id).delete()
        session.flush()

        now = datetime.now(UTC)

        # --------------------------------------------------------------------------
        # 1. VACANCIES & VERSIONED REQUIREMENT SETS (ALL WITH PUBLISHED CRITERIA)
        # --------------------------------------------------------------------------

        # Vacancy 1: Senior Distributed Systems Engineer (OPEN)
        job1 = Job(
            tenant_id=tenant_id,
            creator_id=user_id,
            hiring_manager_id=user_id,
            title="Senior Distributed Systems Engineer",
            description=(
                "Architect and scale Talvora's durable transactional outbox, partitioned event "
                "streams, and monotonic lease fencing pipelines to sustain high-volume global operations."
            ),
            department="Platform Infrastructure",
            location="Remote (US / Canada)",
            employment_type="FULL_TIME",
            status=JobStatus.OPEN.value,
            version=1,
            created_at=now - timedelta(days=20),
            updated_at=now - timedelta(days=15),
        )
        session.add(job1)
        session.flush()

        req_ver1 = RequirementVersion(
            tenant_id=tenant_id,
            job_id=job1.id,
            version_number=1,
            title="Senior Distributed Systems Baseline Criteria v1",
            description="Core engineering criteria governing distributed transaction and streaming reliability.",
            published_by=user_id,
            published_at=now - timedelta(days=15),
            change_summary="Initial publication of distributed systems requirements framework.",
        )
        session.add(req_ver1)
        session.flush()
        job1.current_published_version_id = req_ver1.id
        job1.current_published_version_number = 1

        crit1_1 = Criterion(
            tenant_id=tenant_id,
            requirement_version_id=req_ver1.id,
            criterion_type=CriterionType.MIN_EXPERIENCE_MONTHS.value,
            is_required=True,
            source_requirement_text="Minimum 5 years (60 months) building high-concurrency backend services.",
            relevance_rationale="Essential for designing distributed consensus and resolving complex concurrency races.",
            order_index=0,
            configuration={
                "criterion_type": "MIN_EXPERIENCE_MONTHS",
                "min_months": 60,
                "relevant_titles": ["Distributed Systems Engineer", "Senior Backend Engineer", "Infrastructure Engineer"],
            },
        )
        crit1_2 = Criterion(
            tenant_id=tenant_id,
            requirement_version_id=req_ver1.id,
            criterion_type=CriterionType.SKILL_PRESENT.value,
            is_required=True,
            source_requirement_text="Demonstrated expertise in Python 3.12+, FastAPI, and SQLAlchemy async query execution.",
            relevance_rationale="Primary backend architecture is built upon Python and ASGI.",
            order_index=1,
            configuration={
                "criterion_type": "SKILL_PRESENT",
                "skill_name": "Python & AsyncIO",
                "canonical_name": "python",
                "synonyms": ["python3", "asyncio", "fastapi"],
            },
        )
        crit1_3 = Criterion(
            tenant_id=tenant_id,
            requirement_version_id=req_ver1.id,
            criterion_type=CriterionType.SKILL_PRESENT.value,
            is_required=False,
            source_requirement_text="Experience with AWS SQS, event choreography, and Apache Kafka or RabbitMQ.",
            relevance_rationale="Enables rapid ramp-up on our transactional outbox infrastructure.",
            order_index=2,
            configuration={
                "criterion_type": "SKILL_PRESENT",
                "skill_name": "Event Streaming & Queues",
                "canonical_name": "sqs",
                "synonyms": ["kafka", "rabbitmq", "sqs"],
            },
        )
        session.add_all([crit1_1, crit1_2, crit1_3])

        # Vacancy 2: Staff Machine Learning & LLM Systems Architect (OPEN)
        job2 = Job(
            tenant_id=tenant_id,
            creator_id=user_id,
            hiring_manager_id=user_id,
            title="Staff Machine Learning & LLM Systems Architect",
            description=(
                "Lead the design of bounded, hallucination-resistant LLM analysis engines and cited "
                "evidence extraction pipelines with exact Unicode offset grounding."
            ),
            department="Core AI Research",
            location="San Francisco, CA (Hybrid)",
            employment_type="FULL_TIME",
            status=JobStatus.OPEN.value,
            version=1,
            created_at=now - timedelta(days=25),
            updated_at=now - timedelta(days=18),
        )
        session.add(job2)
        session.flush()

        req_ver2 = RequirementVersion(
            tenant_id=tenant_id,
            job_id=job2.id,
            version_number=1,
            title="Staff AI Architect Evaluation Framework v1",
            description="Defines technical criteria for evaluation, RAG pipelines, and model safety.",
            published_by=user_id,
            published_at=now - timedelta(days=18),
            change_summary="Published requirement set for AI Architect vacancy.",
        )
        session.add(req_ver2)
        session.flush()
        job2.current_published_version_id = req_ver2.id
        job2.current_published_version_number = 1

        crit2_1 = Criterion(
            tenant_id=tenant_id,
            requirement_version_id=req_ver2.id,
            criterion_type=CriterionType.MIN_EXPERIENCE_MONTHS.value,
            is_required=True,
            source_requirement_text="Minimum 6 years (72 months) in applied machine learning, NLP, or production LLM systems.",
            relevance_rationale="Required for architectural leadership in complex multi-modal extraction pipelines.",
            order_index=0,
            configuration={
                "criterion_type": "MIN_EXPERIENCE_MONTHS",
                "min_months": 72,
                "relevant_titles": ["Staff ML Engineer", "AI Architect", "Principal Research Scientist"],
            },
        )
        crit2_2 = Criterion(
            tenant_id=tenant_id,
            requirement_version_id=req_ver2.id,
            criterion_type=CriterionType.SKILL_PRESENT.value,
            is_required=True,
            source_requirement_text="Production mastery of PyTorch, vector databases, and deterministic retrieval pipelines.",
            relevance_rationale="Powers the citation and evidence grounding mechanisms.",
            order_index=1,
            configuration={
                "criterion_type": "SKILL_PRESENT",
                "skill_name": "Machine Learning & RAG",
                "canonical_name": "pytorch",
                "synonyms": ["pytorch", "transformers", "rag"],
            },
        )
        session.add_all([crit2_1, crit2_2])

        # Vacancy 3: Senior Full Stack Engineer (OPEN)
        job3 = Job(
            tenant_id=tenant_id,
            creator_id=user_id,
            hiring_manager_id=user_id,
            title="Senior Full Stack Engineer (React & TypeScript)",
            description=(
                "Build modern, accessible talent operations workspaces using React 19, TypeScript, "
                "and high-density operational data tables with zero layout jitter."
            ),
            department="Product Engineering",
            location="New York, NY (Hybrid)",
            employment_type="FULL_TIME",
            status=JobStatus.OPEN.value,
            version=1,
            created_at=now - timedelta(days=30),
            updated_at=now - timedelta(days=22),
        )
        session.add(job3)
        session.flush()

        req_ver3 = RequirementVersion(
            tenant_id=tenant_id,
            job_id=job3.id,
            version_number=1,
            title="Full Stack UI/API Qualifications v1",
            description="Frontend performance and design system standards.",
            published_by=user_id,
            published_at=now - timedelta(days=22),
            change_summary="Approved candidate requirements for Frontend/Fullstack.",
        )
        session.add(req_ver3)
        session.flush()
        job3.current_published_version_id = req_ver3.id
        job3.current_published_version_number = 1

        crit3_1 = Criterion(
            tenant_id=tenant_id,
            requirement_version_id=req_ver3.id,
            criterion_type=CriterionType.SKILL_PRESENT.value,
            is_required=True,
            source_requirement_text="Advanced TypeScript, React component lifecycle, and reactive state management.",
            relevance_rationale="Core stack for the recruiter desk application.",
            order_index=0,
            configuration={
                "criterion_type": "SKILL_PRESENT",
                "skill_name": "React & TypeScript",
                "canonical_name": "react",
                "synonyms": ["react", "typescript", "vite"],
            },
        )
        crit3_2 = Criterion(
            tenant_id=tenant_id,
            requirement_version_id=req_ver3.id,
            criterion_type=CriterionType.SKILL_PRESENT.value,
            is_required=False,
            source_requirement_text="Demonstrated compliance with WCAG 2.1 AA accessibility standards.",
            relevance_rationale="Ensures universal desk usability across keyboard navigation and assistive devices.",
            order_index=1,
            configuration={
                "criterion_type": "SKILL_PRESENT",
                "skill_name": "Accessibility (a11y)",
                "canonical_name": "a11y",
                "synonyms": ["wcag", "aria", "accessibility"],
            },
        )
        session.add_all([crit3_1, crit3_2])

        # Vacancy 4: Principal SRE & Cloud Security Architect (OPEN)
        job4 = Job(
            tenant_id=tenant_id,
            creator_id=user_id,
            hiring_manager_id=user_id,
            title="Principal Site Reliability & Cloud Security Architect",
            description=(
                "Establish hardened defense-in-depth boundaries, multi-region database failover, "
                "and ephemeral credential rotations across AWS sovereign clusters."
            ),
            department="Cloud Security",
            location="Remote (Global)",
            employment_type="FULL_TIME",
            status=JobStatus.OPEN.value,
            version=1,
            created_at=now - timedelta(days=12),
            updated_at=now - timedelta(days=8),
        )
        session.add(job4)
        session.flush()

        req_ver4 = RequirementVersion(
            tenant_id=tenant_id,
            job_id=job4.id,
            version_number=1,
            title="Cloud Security & Resilience Architecture v1",
            description="Infrastructure resilience, zero-trust policies, and multi-region recovery standards.",
            published_by=user_id,
            published_at=now - timedelta(days=8),
            change_summary="Published production governance criteria for SRE Architect vacancy.",
        )
        session.add(req_ver4)
        session.flush()
        job4.current_published_version_id = req_ver4.id
        job4.current_published_version_number = 1

        crit4_1 = Criterion(
            tenant_id=tenant_id,
            requirement_version_id=req_ver4.id,
            criterion_type=CriterionType.MIN_EXPERIENCE_MONTHS.value,
            is_required=True,
            source_requirement_text="Minimum 7 years (84 months) leading SRE or Cloud Platform Security initiatives.",
            relevance_rationale="Guarantees deep architectural ownership of mission-critical sovereign clusters.",
            order_index=0,
            configuration={
                "criterion_type": "MIN_EXPERIENCE_MONTHS",
                "min_months": 84,
                "relevant_titles": ["Principal SRE", "Cloud Security Architect", "Staff Infrastructure Engineer"],
            },
        )
        crit4_2 = Criterion(
            tenant_id=tenant_id,
            requirement_version_id=req_ver4.id,
            criterion_type=CriterionType.SKILL_PRESENT.value,
            is_required=True,
            source_requirement_text="Mastery of Kubernetes (EKS), Terraform, zero-trust IAM policies, and eBPF.",
            relevance_rationale="Core operational baseline for Talvora's container orchestration plane.",
            order_index=1,
            configuration={
                "criterion_type": "SKILL_PRESENT",
                "skill_name": "Cloud Security & EKS",
                "canonical_name": "kubernetes",
                "synonyms": ["kubernetes", "eks", "terraform", "iam"],
            },
        )
        session.add_all([crit4_1, crit4_2])

        # Vacancy 5: Lead Talent Acquisition Partner (OPEN)
        job5 = Job(
            tenant_id=tenant_id,
            creator_id=user_id,
            hiring_manager_id=user_id,
            title="Lead Talent Acquisition Partner (Executive Search)",
            description=(
                "Manage executive hiring pipelines, leadership assessment frameworks, and "
                "strategic talent outreach across engineering, product, and AI research."
            ),
            department="People Operations",
            location="London / Remote",
            employment_type="FULL_TIME",
            status=JobStatus.OPEN.value,
            version=1,
            created_at=now - timedelta(days=16),
            updated_at=now - timedelta(days=10),
        )
        session.add(job5)
        session.flush()

        req_ver5 = RequirementVersion(
            tenant_id=tenant_id,
            job_id=job5.id,
            version_number=1,
            title="Executive Search & People Operations Standards v1",
            description="Recruiting telemetry, structured debriefing, and leadership compensation frameworks.",
            published_by=user_id,
            published_at=now - timedelta(days=10),
            change_summary="Published criteria governing senior talent acquisition operations.",
        )
        session.add(req_ver5)
        session.flush()
        job5.current_published_version_id = req_ver5.id
        job5.current_published_version_number = 1

        crit5_1 = Criterion(
            tenant_id=tenant_id,
            requirement_version_id=req_ver5.id,
            criterion_type=CriterionType.MIN_EXPERIENCE_MONTHS.value,
            is_required=True,
            source_requirement_text="Minimum 6 years (72 months) driving technical or executive talent acquisition.",
            relevance_rationale="Crucial for engaging staff+ engineering leadership candidates.",
            order_index=0,
            configuration={
                "criterion_type": "MIN_EXPERIENCE_MONTHS",
                "min_months": 72,
                "relevant_titles": ["Lead Recruiter", "Executive Recruiter", "Talent Acquisition Lead"],
            },
        )
        crit5_2 = Criterion(
            tenant_id=tenant_id,
            requirement_version_id=req_ver5.id,
            criterion_type=CriterionType.SKILL_PRESENT.value,
            is_required=True,
            source_requirement_text="Expertise in ATS compliance, candidate pipeline telemetry, and executive offer negotiation.",
            relevance_rationale="Ensures ethical hiring velocity and high offer acceptance rates.",
            order_index=1,
            configuration={
                "criterion_type": "SKILL_PRESENT",
                "skill_name": "Executive Talent Operations",
                "canonical_name": "recruiting",
                "synonyms": ["sourcing", "ats", "negotiation"],
            },
        )
        session.add_all([crit5_1, crit5_2])
        session.flush()

        # --------------------------------------------------------------------------
        # 2. REALISTIC CANDIDATES & CONTACT HISTORIES
        # --------------------------------------------------------------------------
        candidate_data = [
            (
                "Elena Rostova",
                "elena.rostova@cloudscale.io",
                "+1 (415) 890-2341",
                CandidateSource.UPLOAD_INTAKE.value,
                True,
            ),
            (
                "Marcus Vance",
                "marcus.vance@distributedkernel.net",
                "+1 (206) 555-0182",
                CandidateSource.MANUAL_ENTRY.value,
                True,
            ),
            (
                "Priya Sharma",
                "priya.sharma@mlfoundations.org",
                "+1 (650) 441-9872",
                CandidateSource.UPLOAD_INTAKE.value,
                True,
            ),
            (
                "Liam O'Connor",
                "liam.oconnor@modernstack.dev",
                "+1 (617) 320-1944",
                CandidateSource.UPLOAD_INTAKE.value,
                True,
            ),
            (
                "David Chen",
                "david.chen@enterprisecloud.com",
                "+1 (212) 809-5412",
                CandidateSource.MANUAL_ENTRY.value,
                False,  # Contact Unverified to demonstrate human verification desk
            ),
            (
                "Sophia Patel",
                "sophia.patel@algorithmicops.ai",
                "+1 (408) 789-6631",
                CandidateSource.UPLOAD_INTAKE.value,
                True,
            ),
            (
                "Alex Rivera",
                "alex.rivera@systems-infra.io",
                "+1 (512) 990-4123",
                CandidateSource.MANUAL_ENTRY.value,
                True,
            ),
            (
                "Maya Lin",
                "maya.lin@frontendcraft.co",
                "+1 (312) 670-8819",
                CandidateSource.UPLOAD_INTAKE.value,
                True,
            ),
        ]

        candidates: list[Candidate] = []
        for name, email, phone, source, verified in candidate_data:
            cand = Candidate(
                tenant_id=tenant_id,
                created_by=user_id,
                display_name=name,
                normalized_name=name.lower(),
                email=email,
                normalized_email=email.lower(),
                phone=phone,
                source=source,
                is_draft=False,
                current_contact_revision_number=1,
                verified_contact_revision_number=1 if verified else None,
                created_at=now - timedelta(days=14),
                updated_at=now - timedelta(days=2),
            )
            session.add(cand)
            session.flush()

            rev = CandidateContactRevision(
                tenant_id=tenant_id,
                candidate_id=cand.id,
                revision_number=1,
                display_name=name,
                normalized_name=name.lower(),
                email=email,
                normalized_email=email.lower(),
                phone=phone,
                source=ContactSource.RECRUITER_ENTERED.value
                if source == CandidateSource.MANUAL_ENTRY.value
                else ContactSource.RESUME_EXTRACTED_UNVERIFIED.value,
                created_by=user_id,
                created_at=now - timedelta(days=14),
            )
            session.add(rev)
            session.flush()

            cand.current_contact_revision_id = rev.id
            if verified:
                cand.verified_contact_revision_id = rev.id
                verif = CandidateContactVerification(
                    tenant_id=tenant_id,
                    candidate_id=cand.id,
                    contact_revision_id=rev.id,
                    contact_revision_number=1,
                    verified_address=email,
                    verification_source=ContactVerificationSource.RECRUITER_ATTESTATION.value,
                    notes="Recruiter confirmed direct email and phone authenticity during intake.",
                    actor_id=user_id,
                    created_at=now - timedelta(days=12),
                )
                session.add(verif)

            candidates.append(cand)

        session.flush()

        # --------------------------------------------------------------------------
        # 3. HIRING PIPELINE APPLICATIONS & IMMUTABLE STAGE HISTORIES
        # --------------------------------------------------------------------------

        # Elena -> Distributed Systems (INTERVIEWING)
        app1 = Application(
            tenant_id=tenant_id,
            job_id=job1.id,
            candidate_id=candidates[0].id,
            stage=ApplicationStage.INTERVIEWING.value,
            source=ApplicationSource.UPLOAD_INTAKE.value,
            notes="Passed distributed systems architecture screening. Panel interviews scheduled.",
            created_by=user_id,
            created_at=now - timedelta(days=9),
            updated_at=now - timedelta(days=3),
        )
        session.add(app1)
        session.flush()

        evt1_1 = StageEvent(
            tenant_id=tenant_id,
            application_id=app1.id,
            from_stage=None,
            to_stage=ApplicationStage.NEW.value,
            reason="Resume uploaded and extracted via quarantine pipeline.",
            actor_id=user_id,
            actor_role="RECRUITER",
            expected_version=1,
            created_at=now - timedelta(days=9),
        )
        evt1_2 = StageEvent(
            tenant_id=tenant_id,
            application_id=app1.id,
            from_stage=ApplicationStage.NEW.value,
            to_stage=ApplicationStage.IN_REVIEW.value,
            reason="Initial criteria verification matched 6+ years backend experience.",
            actor_id=user_id,
            actor_role="RECRUITER",
            expected_version=2,
            created_at=now - timedelta(days=7),
        )
        evt1_3 = StageEvent(
            tenant_id=tenant_id,
            application_id=app1.id,
            from_stage=ApplicationStage.IN_REVIEW.value,
            to_stage=ApplicationStage.INTERVIEW_APPROVED.value,
            reason="Hiring Manager approved candidate for technical rounds.",
            actor_id=user_id,
            actor_role="HIRING_MANAGER",
            expected_version=3,
            created_at=now - timedelta(days=5),
        )
        evt1_4 = StageEvent(
            tenant_id=tenant_id,
            application_id=app1.id,
            from_stage=ApplicationStage.INTERVIEW_APPROVED.value,
            to_stage=ApplicationStage.INTERVIEWING.value,
            reason="System design loop scheduled with Principal SRE.",
            actor_id=user_id,
            actor_role="RECRUITER",
            expected_version=4,
            created_at=now - timedelta(days=3),
        )
        session.add_all([evt1_1, evt1_2, evt1_3, evt1_4])

        # Marcus -> Distributed Systems (DECISION_PENDING)
        app2 = Application(
            tenant_id=tenant_id,
            job_id=job1.id,
            candidate_id=candidates[1].id,
            stage=ApplicationStage.DECISION_PENDING.value,
            source=ApplicationSource.MANUAL_ENTRY.value,
            notes="Completed 4-stage interview loop. Exceptional consensus algorithm proficiency.",
            created_by=user_id,
            created_at=now - timedelta(days=12),
            updated_at=now - timedelta(days=1),
        )
        session.add(app2)
        session.flush()

        evt2_1 = StageEvent(
            tenant_id=tenant_id,
            application_id=app2.id,
            from_stage=None,
            to_stage=ApplicationStage.NEW.value,
            reason="Direct recruiter referral intake.",
            actor_id=user_id,
            actor_role="RECRUITER",
            expected_version=1,
            created_at=now - timedelta(days=12),
        )
        evt2_2 = StageEvent(
            tenant_id=tenant_id,
            application_id=app2.id,
            from_stage=ApplicationStage.NEW.value,
            to_stage=ApplicationStage.INTERVIEWING.value,
            reason="Fast-tracked due to open-source contributions to Raft consensus.",
            actor_id=user_id,
            actor_role="RECRUITER",
            expected_version=2,
            created_at=now - timedelta(days=8),
        )
        evt2_3 = StageEvent(
            tenant_id=tenant_id,
            application_id=app2.id,
            from_stage=ApplicationStage.INTERVIEWING.value,
            to_stage=ApplicationStage.DECISION_PENDING.value,
            reason="Final debrief completed. Awaiting compensation committee sign-off.",
            actor_id=user_id,
            actor_role="HIRING_MANAGER",
            expected_version=3,
            created_at=now - timedelta(days=1),
        )
        session.add_all([evt2_1, evt2_2, evt2_3])

        # Priya -> AI Systems Architect (INTERVIEW_APPROVED)
        app3 = Application(
            tenant_id=tenant_id,
            job_id=job2.id,
            candidate_id=candidates[2].id,
            stage=ApplicationStage.INTERVIEW_APPROVED.value,
            source=ApplicationSource.UPLOAD_INTAKE.value,
            notes="Demonstrated 8 years of applied NLP & high-throughput inference serving.",
            created_by=user_id,
            created_at=now - timedelta(days=6),
            updated_at=now - timedelta(days=2),
        )
        session.add(app3)
        session.flush()

        evt3_1 = StageEvent(
            tenant_id=tenant_id,
            application_id=app3.id,
            from_stage=None,
            to_stage=ApplicationStage.NEW.value,
            reason="Inbound application parsed from resume.",
            actor_id=user_id,
            actor_role="RECRUITER",
            expected_version=1,
            created_at=now - timedelta(days=6),
        )
        evt3_2 = StageEvent(
            tenant_id=tenant_id,
            application_id=app3.id,
            from_stage=ApplicationStage.NEW.value,
            to_stage=ApplicationStage.INTERVIEW_APPROVED.value,
            reason="Strong citation evidence on distributed model inference.",
            actor_id=user_id,
            actor_role="HIRING_MANAGER",
            expected_version=2,
            created_at=now - timedelta(days=2),
        )
        session.add_all([evt3_1, evt3_2])

        # Liam -> Senior Full Stack (HIRED)
        app4 = Application(
            tenant_id=tenant_id,
            job_id=job3.id,
            candidate_id=candidates[3].id,
            stage=ApplicationStage.HIRED.value,
            source=ApplicationSource.UPLOAD_INTAKE.value,
            notes="Offer formally accepted. Onboarding workflow initiated.",
            created_by=user_id,
            created_at=now - timedelta(days=20),
            updated_at=now - timedelta(hours=4),
        )
        session.add(app4)
        session.flush()

        evt4_1 = StageEvent(
            tenant_id=tenant_id,
            application_id=app4.id,
            from_stage=ApplicationStage.DECISION_PENDING.value,
            to_stage=ApplicationStage.HIRED.value,
            reason="Candidate signed offer letter. Background screening verified.",
            actor_id=user_id,
            actor_role="RECRUITER",
            expected_version=1,
            created_at=now - timedelta(hours=4),
        )
        session.add(evt4_1)

        # David -> Distributed Systems (NEW)
        app5 = Application(
            tenant_id=tenant_id,
            job_id=job1.id,
            candidate_id=candidates[4].id,
            stage=ApplicationStage.NEW.value,
            source=ApplicationSource.MANUAL_ENTRY.value,
            notes="Requires contact verification before scheduling initial phone screen.",
            created_by=user_id,
            created_at=now - timedelta(hours=6),
            updated_at=now - timedelta(hours=6),
        )
        session.add(app5)
        session.flush()

        # Sophia -> AI Systems Architect (IN_REVIEW)
        app6 = Application(
            tenant_id=tenant_id,
            job_id=job2.id,
            candidate_id=candidates[5].id,
            stage=ApplicationStage.IN_REVIEW.value,
            source=ApplicationSource.UPLOAD_INTAKE.value,
            notes="Recruiter review of multi-agent evaluation benchmark research.",
            created_by=user_id,
            created_at=now - timedelta(days=4),
            updated_at=now - timedelta(days=1),
        )
        session.add(app6)
        session.flush()

        # Alex -> Distributed Systems (REJECTED)
        app7 = Application(
            tenant_id=tenant_id,
            job_id=job1.id,
            candidate_id=candidates[6].id,
            stage=ApplicationStage.REJECTED.value,
            source=ApplicationSource.MANUAL_ENTRY.value,
            notes="Does not meet mandatory 60 months distributed systems requirement.",
            created_by=user_id,
            created_at=now - timedelta(days=11),
            updated_at=now - timedelta(days=9),
        )
        session.add(app7)
        session.flush()

        evt7_1 = StageEvent(
            tenant_id=tenant_id,
            application_id=app7.id,
            from_stage=ApplicationStage.IN_REVIEW.value,
            to_stage=ApplicationStage.REJECTED.value,
            reason="Missing required distributed transactions and consensus experience.",
            actor_id=user_id,
            actor_role="RECRUITER",
            expected_version=1,
            created_at=now - timedelta(days=9),
        )
        session.add(evt7_1)

        # Maya -> Full Stack (IN_REVIEW)
        app8 = Application(
            tenant_id=tenant_id,
            job_id=job3.id,
            candidate_id=candidates[7].id,
            stage=ApplicationStage.IN_REVIEW.value,
            source=ApplicationSource.UPLOAD_INTAKE.value,
            notes="Design system and UI accessibility review in progress.",
            created_by=user_id,
            created_at=now - timedelta(days=3),
            updated_at=now - timedelta(days=1),
        )
        session.add(app8)
        session.flush()

        # --------------------------------------------------------------------------
        # 4. DOCUMENTS, EXTRACTION RUNS & CITATION SOURCE SEGMENTS (FOR EVIDENCE DESK)
        # --------------------------------------------------------------------------
        doc_definitions = [
            {
                "id": uuid.UUID("a1b2c3d4-e5f6-4a1b-8c2d-3e4f5a6b7c8d"),
                "candidate": candidates[0],  # Elena Rostova
                "application": app1,
                "filename": "Elena_Rostova_Resume_2026.pdf",
                "segments": [
                    (
                        1,
                        "ELENA ROSTOVA — Principal Systems & Cloud Infrastructure Architect\n"
                        "San Francisco, CA · elena.rostova@cloudscale.io · +1 (415) 890-2341\n\n"
                        "PROFESSIONAL SUMMARY\n"
                        "Over 8 years of distributed systems engineering and cloud security leadership. "
                        "Spearheaded enterprise-scale multi-region infrastructure migrations, zero-trust network boundaries, "
                        "and real-time Kafka event streaming pipelines across Tier-0 financial services.\n\n"
                        "CORE COMPETENCIES\n"
                        "• Distributed Systems: Raft consensus, transactional outbox patterns, partitioned log architectures.\n"
                        "• Cloud & Security: AWS (EKS, IAM, KMS, GuardDuty), Terraform, zero-trust mutual TLS.\n"
                        "• Languages & Frameworks: Python (FastAPI, AsyncIO), Rust, Go, PostgreSQL, Redis."
                    ),
                    (
                        2,
                        "EXPERIENCE & ACCOMPLISHMENTS\n"
                        "Staff Infrastructure Architect — CloudScale IO (2022 – Present)\n"
                        "• Designed and deployed multi-region active-active database failover across AWS us-east-1 and eu-west-1, "
                        "sustaining 99.999% SLA across 40M daily transactions.\n"
                        "• Authored high-throughput transactional outbox service in Python 3.12 and AsyncIO with monotonic lease fencing, "
                        "eliminating split-brain anomalies under network partitions.\n"
                        "• Implemented automated credential rotation and secret-less container identity with HashiCorp Vault and SPIFFE/SPIRE.\n\n"
                        "Senior Distributed Systems Engineer — DataStream Core (2018 – 2022)\n"
                        "• Scaled Apache Kafka and RabbitMQ pipelines to ingest 1.2 billion events daily with sub-10ms p99 processing latency.\n"
                        "• Mentored 14 backend and infrastructure engineers in concurrent memory management and distributed debugging."
                    ),
                ],
            },
            {
                "id": uuid.UUID("b2c3d4e5-f6a1-4b2c-9d3e-4f5a6b7c8d9e"),
                "candidate": candidates[1],  # Marcus Vance
                "application": app2,
                "filename": "Marcus_Vance_Staff_Distributed_Systems.pdf",
                "segments": [
                    (
                        1,
                        "MARCUS VANCE — Senior Staff Distributed Systems Engineer\n"
                        "Seattle, WA · marcus.vance@distributedkernel.net · +1 (206) 555-0182\n\n"
                        "EXECUTIVE BIO\n"
                        "Distributed systems specialist with 7+ years optimizing consensus protocols, transactional outboxes, "
                        "and high-concurrency low-latency network primitives. Core open-source contributor to Paxos and Raft implementations.\n\n"
                        "TECHNICAL SKILLS\n"
                        "• Systems: Python, C++, Go, AsyncIO, Linux kernel internals, TCP/IP socket tuning.\n"
                        "• Storage & Queues: PostgreSQL ACID internals, Apache Kafka, RocksDB, AWS SQS."
                    ),
                    (
                        2,
                        "SELECTED CONTRIBUTIONS\n"
                        "Principal Engineer — Distributed Kernel Labs (2021 – Present)\n"
                        "• Architected fault-tolerant state-machine replication cluster with zero data loss under simulated chaos partitions.\n"
                        "• Optimized Python ASGI query execution engine reducing CPU cache misses by 28% under 50k RPS load.\n"
                        "• Designed automated canary verification harnesses and crash recovery testing protocols."
                    ),
                ],
            },
            {
                "id": uuid.UUID("c3d4e5f6-a1b2-4c3d-ae4f-5a6b7c8d9e0f"),
                "candidate": candidates[2],  # Priya Sharma
                "application": app3,
                "filename": "Priya_Sharma_Principal_AI_Architect.pdf",
                "segments": [
                    (
                        1,
                        "PRIYA SHARMA, Ph.D. — Staff Machine Learning & LLM Systems Architect\n"
                        "Palo Alto, CA · priya.sharma@mlfoundations.org · +1 (650) 441-9872\n\n"
                        "SPECIALIZATION\n"
                        "Applied AI research architect focusing on bounded hallucination mitigation, deterministic grounding with exact Unicode offsets, "
                        "and low-latency inference serving for LLM agents.\n\n"
                        "RESEARCH & INDUSTRY EXPERIENCE\n"
                        "Lead AI Architect — ML Foundations Org (2020 – Present)\n"
                        "• Invented deterministic cited-retrieval pipeline pairing PyTorch vector search with source span verification, "
                        "reducing citation mismatch error to 0.01%.\n"
                        "• Published 4 peer-reviewed papers on trustworthy agentic reasoning and evaluation metrics.\n"
                        "• Scaled model serving infrastructure on NVIDIA Triton and vLLM delivering 400 tokens/sec across 200 concurrent user sessions."
                    ),
                ],
            },
        ]

        for doc_item in doc_definitions:
            doc = Document(
                id=doc_item["id"],
                tenant_id=tenant_id,
                candidate_id=doc_item["candidate"].id,
                latest_version_number=1,
                created_by=user_id,
                version=1,
                created_at=now - timedelta(days=7),
                updated_at=now - timedelta(days=7),
            )
            session.add(doc)
            session.flush()

            raw_bytes = ("\n\n".join(seg[1] for seg in doc_item["segments"])).encode("utf-8")
            checksum = hashlib.sha256(raw_bytes).hexdigest()

            doc_ver = DocumentVersion(
                id=uuid.uuid4(),
                tenant_id=tenant_id,
                document_id=doc.id,
                version_number=1,
                sha256_checksum=checksum,
                byte_size=len(raw_bytes),
                media_type="application/pdf",
                storage_bucket="talvora-clean",
                storage_key=f"tenants/{tenant_id}/documents/{doc.id}/v1.pdf",
                storage_state=DocumentVersionStorageState.CLEAN.value,
                scan_result="CLEAN",
                source_actor_id=user_id,
                created_at=now - timedelta(days=7),
            )
            session.add(doc_ver)
            session.flush()

            # Bind to application
            app_doc = ApplicationDocument(
                id=uuid.uuid4(),
                tenant_id=tenant_id,
                application_id=doc_item["application"].id,
                document_id=doc.id,
                document_version_id=doc_ver.id,
                binding_generation=1,
                status=ApplicationDocumentStatus.ACTIVE.value,
                bound_by=user_id,
                bound_at=now - timedelta(days=7),
                created_at=now - timedelta(days=7),
            )
            session.add(app_doc)

            # Extraction run (SUCCEEDED)
            total_chars = sum(len(seg[1]) for seg in doc_item["segments"])
            run = ExtractionRun(
                id=uuid.uuid4(),
                tenant_id=tenant_id,
                document_id=doc.id,
                document_version_id=doc_ver.id,
                revision_number=1,
                extractor_version="pdf-pypdf5-pypdfium2-tesseract-v1",
                normalization_version="norm-v1",
                media_type="application/pdf",
                status=ExtractionRunStatus.SUCCEEDED.value,
                problem_code=None,
                is_complete=True,
                total_pages=len(doc_item["segments"]),
                processed_pages=len(doc_item["segments"]),
                ocr_pages_used=0,
                total_characters=total_chars,
                triggered_by="INTAKE_AUTO_EXTRACTION",
                requested_by_actor_id=user_id,
                started_at=now - timedelta(days=7),
                completed_at=now - timedelta(days=7),
                version=1,
                created_at=now - timedelta(days=7),
                updated_at=now - timedelta(days=7),
            )
            session.add(run)
            session.flush()

            # Source segments
            for idx, (pg_num, text_content) in enumerate(doc_item["segments"]):
                seg_sha = hashlib.sha256(text_content.encode("utf-8")).hexdigest()
                segment = SourceSegment(
                    id=uuid.uuid4(),
                    tenant_id=tenant_id,
                    extraction_run_id=run.id,
                    document_version_id=doc_ver.id,
                    segment_index=idx,
                    locator_type=SourceLocatorType.PDF_PAGE.value,
                    page_number=pg_num,
                    extraction_method=SegmentExtractionMethod.DIGITAL_TEXT.value,
                    text=text_content,
                    char_count=len(text_content),
                    canonical_text_sha256=seg_sha,
                    is_low_confidence=False,
                    created_at=now - timedelta(days=7),
                )
                session.add(segment)

        session.commit()
        print("Successfully seeded realistic enterprise recruitment dataset!")
        print(f"Tenancy: {org.name} ({org.id})")
        print(f"Vacancies: 5 (All Open with published requirement versions and criteria)")
        print(f"Candidates: {len(candidates)} (7 verified, 1 unverified)")
        print(f"Applications: 8 across NEW, IN_REVIEW, INTERVIEW_APPROVED, INTERVIEWING, DECISION_PENDING, HIRED, REJECTED")
        print(f"Documents: 3 with full text extractions and source segments ready on Evidence desk")


if __name__ == "__main__":
    seed()
