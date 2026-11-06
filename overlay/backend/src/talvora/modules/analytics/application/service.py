"""Deterministic Pipeline, Operations, and AI Usage Analytics Service (P17).

Key guarantees:
- Time-in-stage and transition counts are computed from immutable `stage_event` history
  rather than mutable `application.stage` alone.
- Application, candidate, and interview denominators are reported separately.
- Scoped strictly to assigned jobs/teams for RECRUITER and HIRING_MANAGER; OWNER/ADMIN
  can query tenant-wide or filtered by `job_id` / `team_id`.
- Enforces configurable minimum cohort threshold (`min_cohort_size`, default 5, floor 3)
  on sensitive small-group rate/latency distributions, paired with explicit disclosure.
- Strictly excludes candidate ranking, composite scores, or demographic inference.
"""

import statistics
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import sqlalchemy as sa
from sqlalchemy import func, select
from sqlalchemy import inspect as sa_inspect

from talvora.modules.analytics.domain.models import (
    ANALYTICS_GOVERNANCE_DISCLOSURE,
    COHORT_SUPPRESSION_DISCLOSURE,
    UsageCounter,
)
from talvora.modules.applications.domain.models import (
    Application,
    ApplicationStage,
    StageEvent,
)
from talvora.modules.documents.domain.models import (
    DocumentVersion,
    UploadItem,
    UploadItemStatus,
)
from talvora.modules.evidence.domain.models import ExtractionRun, ExtractionRunStatus
from talvora.modules.feedback.domain.models import (
    ExportArtifact,
    ExportStatus,
    Feedback,
    FeedbackStatus,
)
from talvora.modules.identity.domain.roles import Role
from talvora.modules.interview_packs.domain.models import (
    InterviewPackContext,
    PackRevision,
)
from talvora.modules.jobs.domain.models import Job, JobAssignment, JobRole
from talvora.modules.operations.domain.models import JobOperation, OperationStatus
from talvora.modules.organizations.domain.models import Membership
from talvora.modules.screening.domain.models import (
    ScreeningOverride,
    ScreeningResult,
    ScreeningRun,
)
from talvora.modules.summaries.domain.models import CandidateSummaryRevision
from talvora.shared.application.actor import ActorContext
from talvora.shared.infrastructure.uow import UnitOfWork
from talvora.shared.transport.problems import ProblemException


def _ensure_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def _resolve_timezone(tz_name: str) -> ZoneInfo:
    try:
        return ZoneInfo(tz_name)
    except ZoneInfoNotFoundError as exc:
        raise ProblemException(
            status_code=422,
            title="Invalid IANA Timezone",
            code="VALIDATION_ERROR",
            detail=f"Timezone '{tz_name}' is not a valid IANA timezone identifier.",
        ) from exc


def _resolve_window(
    now_utc: datetime,
    from_utc: datetime | None,
    to_utc: datetime | None,
) -> tuple[datetime, datetime]:
    end = _ensure_utc(to_utc) if to_utc else _ensure_utc(now_utc)
    start = _ensure_utc(from_utc) if from_utc else (end - timedelta(days=30))
    if start > end:
        raise ProblemException(
            status_code=422,
            title="Invalid Analytics Window",
            code="VALIDATION_ERROR",
            detail="from_utc must be earlier than or equal to to_utc.",
        )
    return start, end


def _read_org_analytics_policy(uow: UnitOfWork, tenant_id: UUID) -> tuple[int, bool, int]:
    """Returns `(min_cohort_size, ai_processing_enabled, monthly_ai_token_budget)`."""
    try:
        tables = set(sa_inspect(uow.session.connection()).get_table_names())
    except Exception:
        return 5, False, 1_000_000

    if "organization_policy" not in tables:
        return 5, False, 1_000_000

    cols = {
        c["name"] for c in sa_inspect(uow.session.connection()).get_columns("organization_policy")
    }
    if "min_analytics_cohort_size" not in cols:
        return 5, False, 1_000_000

    row = (
        uow.session.execute(
            sa.text(
                "SELECT min_analytics_cohort_size, ai_processing_enabled, "
                "monthly_ai_token_budget FROM organization_policy WHERE tenant_id = :tid"
            ),
            {"tid": str(tenant_id)},
        )
        .mappings()
        .first()
    )
    if row is None:
        return 5, False, 1_000_000
    return (
        max(3, int(row["min_analytics_cohort_size"])),
        bool(row["ai_processing_enabled"]),
        int(row["monthly_ai_token_budget"]),
    )


def _resolve_scoped_job_ids(
    uow: UnitOfWork,
    actor: ActorContext,
    job_id: UUID | None = None,
    team_id: UUID | None = None,
) -> tuple[UUID, list[UUID]]:
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
            detail="Active organization context is required.",
        )

    tenant_id = actor.tenant_id
    membership = uow.session.scalar(
        select(Membership).where(
            Membership.tenant_id == tenant_id,
            Membership.user_id == actor.user_id,
        )
    )
    if membership is None or membership.status != "ACTIVE":
        raise ProblemException(
            status_code=403,
            title="Forbidden",
            code="FORBIDDEN",
            detail="Active organization membership is required.",
        )
    if membership.role == Role.INTERVIEWER.value:
        raise ProblemException(
            status_code=403,
            title="Forbidden",
            code="FORBIDDEN",
            detail="Interviewers are not authorized to view organization or pipeline analytics.",
        )

    stmt = select(Job.id).where(Job.tenant_id == tenant_id)
    if job_id is not None:
        stmt = stmt.where(Job.id == job_id)
    if team_id is not None:
        stmt = stmt.where(Job.team_id == team_id)

    is_org_admin = membership.role in {Role.OWNER.value, Role.ADMIN.value}
    if not is_org_admin:
        assigned_job_ids = set(
            uow.session.scalars(
                select(JobAssignment.job_id).where(
                    JobAssignment.tenant_id == tenant_id,
                    JobAssignment.user_id == actor.user_id,
                    JobAssignment.role.in_([JobRole.RECRUITER.value, JobRole.HIRING_MANAGER.value]),
                )
            ).all()
        )
        if job_id is not None and job_id not in assigned_job_ids:
            raise ProblemException(
                status_code=403,
                title="Forbidden",
                code="FORBIDDEN",
                detail="You are not assigned as recruiter or hiring manager for the requested job.",
            )
        if not assigned_job_ids:
            return tenant_id, []
        stmt = stmt.where(Job.id.in_(assigned_job_ids))

    return tenant_id, list(uow.session.scalars(stmt).all())


def _percentile(sorted_vals: list[float], pct: float) -> float:
    if not sorted_vals:
        return 0.0
    if len(sorted_vals) == 1:
        return round(sorted_vals[0], 3)
    idx = (len(sorted_vals) - 1) * pct
    lo = int(idx)
    hi = min(lo + 1, len(sorted_vals) - 1)
    frac = idx - lo
    return round(sorted_vals[lo] * (1.0 - frac) + sorted_vals[hi] * frac, 3)


class AnalyticsService:
    """Purely descriptive, definition-backed recruitment pipeline, operations, and usage service."""

    @classmethod
    def record_usage(
        cls,
        uow: UnitOfWork,
        tenant_id: UUID,
        usage_category: str,
        provider_name: str,
        model_id: str,
        prompt_tokens: int,
        completion_tokens: int,
        invocation_count: int = 1,
        job_id: UUID | None = None,
    ) -> UsageCounter:
        now = uow.clock.now()
        row = UsageCounter(
            id=uuid4(),
            tenant_id=tenant_id,
            period_date=now.date(),
            job_id=job_id,
            usage_category=usage_category,
            provider_name=provider_name,
            model_id=model_id,
            invocation_count=max(0, invocation_count),
            prompt_tokens=max(0, prompt_tokens),
            completion_tokens=max(0, completion_tokens),
            total_tokens=max(0, prompt_tokens) + max(0, completion_tokens),
            created_at=now,
            updated_at=now,
        )
        uow.session.add(row)
        return row

    @classmethod
    def get_pipeline_analytics(
        cls,
        uow: UnitOfWork,
        actor: ActorContext,
        job_id: UUID | None = None,
        team_id: UUID | None = None,
        from_utc: datetime | None = None,
        to_utc: datetime | None = None,
        timezone_name: str = "UTC",
    ) -> dict[str, Any]:
        tz = _resolve_timezone(timezone_name)
        now = _ensure_utc(uow.clock.now())
        win_start, win_end = _resolve_window(now, from_utc, to_utc)
        tenant_id, scoped_job_ids = _resolve_scoped_job_ids(uow, actor, job_id, team_id)
        min_cohort, _, _ = _read_org_analytics_policy(uow, tenant_id)

        apps = (
            list(
                uow.session.scalars(
                    select(Application).where(
                        Application.tenant_id == tenant_id,
                        Application.job_id.in_(scoped_job_ids),
                    )
                ).all()
            )
            if scoped_job_ids
            else []
        )
        app_ids = [a.id for a in apps]

        # Separate denominators: applications vs unique candidates vs booked/tracked interviews
        distinct_application_count = len(apps)
        distinct_candidate_count = len({a.candidate_id for a in apps})

        interviews_count = 0
        if app_ids:
            tables = set(sa_inspect(uow.session.connection()).get_table_names())
            if "interview" in tables:
                interviews_count = int(
                    uow.session.execute(
                        sa.text("SELECT COUNT(DISTINCT id) FROM interview WHERE tenant_id = :tid"),
                        {"tid": str(tenant_id)},
                    ).scalar()
                    or 0
                )
            else:
                interviews_count = int(
                    uow.session.scalar(
                        select(func.count(InterviewPackContext.id)).where(
                            InterviewPackContext.tenant_id == tenant_id,
                            InterviewPackContext.application_id.in_(app_ids),
                        )
                    )
                    or 0
                )

        # Fetch all immutable stage_event rows for scoped applications to compute exact dwell durations
        all_events = (
            list(
                uow.session.scalars(
                    select(StageEvent)
                    .where(
                        StageEvent.tenant_id == tenant_id,
                        StageEvent.application_id.in_(app_ids),
                    )
                    .order_by(StageEvent.application_id.asc(), StageEvent.created_at.asc())
                ).all()
            )
            if app_ids
            else []
        )

        events_by_app: dict[UUID, list[StageEvent]] = defaultdict(list)
        for ev in all_events:
            events_by_app[ev.application_id].append(ev)

        entries_in_window: dict[str, int] = defaultdict(int)
        exits_in_window: dict[str, int] = defaultdict(int)
        dwell_hours_by_stage: dict[str, list[float]] = defaultdict(list)
        current_stage_snapshot: dict[str, int] = defaultdict(int)

        for app_row in apps:
            current_stage_snapshot[app_row.stage] += 1
            app_created = _ensure_utc(app_row.created_at)
            evs = events_by_app.get(app_row.id, [])

            # Initial entry into NEW when application was created
            first_stage = (
                evs[0].from_stage if (evs and evs[0].from_stage) else ApplicationStage.NEW.value
            )
            if win_start <= app_created <= win_end:
                entries_in_window[first_stage] += 1

            prev_stage = first_stage
            prev_entered_at = app_created

            for ev in evs:
                ev_ts = _ensure_utc(ev.created_at)
                if win_start <= ev_ts <= win_end:
                    entries_in_window[ev.to_stage] += 1
                    if ev.from_stage:
                        exits_in_window[ev.from_stage] += 1
                        dwell_h = max(0.0, (ev_ts - prev_entered_at).total_seconds() / 3600.0)
                        dwell_hours_by_stage[ev.from_stage].append(dwell_h)
                prev_stage = ev.to_stage
                prev_entered_at = ev_ts

            # Include open dwell time for non-terminal stage if no subsequent exit occurred yet
            if (
                prev_stage
                not in {
                    ApplicationStage.HIRED.value,
                    ApplicationStage.REJECTED.value,
                    ApplicationStage.WITHDRAWN.value,
                }
                and win_start <= prev_entered_at <= win_end
            ):
                open_dwell_h = max(0.0, (win_end - prev_entered_at).total_seconds() / 3600.0)
                dwell_hours_by_stage[prev_stage].append(open_dwell_h)

        stage_metrics: list[dict[str, Any]] = []
        for stage_enum in ApplicationStage:
            st = stage_enum.value
            dwell_list = sorted(dwell_hours_by_stage.get(st, []))
            sample_n = len(dwell_list)
            suppressed = 0 < sample_n < min_cohort
            stage_metrics.append(
                {
                    "stage": st,
                    "current_applications_count": current_stage_snapshot.get(st, 0),
                    "stage_entries_in_window": entries_in_window.get(st, 0),
                    "stage_exits_in_window": exits_in_window.get(st, 0),
                    "dwell_sample_size": sample_n,
                    "suppressed_small_cohort": suppressed,
                    "median_time_in_stage_hours": (
                        None
                        if (sample_n == 0 or suppressed)
                        else round(statistics.median(dwell_list), 3)
                    ),
                    "p90_time_in_stage_hours": (
                        None if (sample_n == 0 or suppressed) else _percentile(dwell_list, 0.90)
                    ),
                }
            )

        return {
            "as_of_utc": now.isoformat(),
            "timezone": timezone_name,
            "window_start_utc": win_start.isoformat(),
            "window_end_utc": win_end.isoformat(),
            "window_start_local": win_start.astimezone(tz).isoformat(),
            "window_end_local": win_end.astimezone(tz).isoformat(),
            "data_freshness": "TRANSACTIONAL_REALTIME",
            "min_cohort_size": min_cohort,
            "scoped_job_count": len(scoped_job_ids),
            "denominators": {
                "distinct_applications_count": distinct_application_count,
                "distinct_candidates_count": distinct_candidate_count,
                "distinct_interviews_count": interviews_count,
            },
            "stages": stage_metrics,
            "metric_definitions": {
                "stage_entries_in_window": "Count of immutable stage_event transitions entering the stage (plus initial NEW creation) within [window_start_utc, window_end_utc].",
                "median_time_in_stage_hours": "Median elapsed hours between stage entry and next stage_event exit (suppressed when 0 < dwell_sample_size < min_cohort_size).",
                "denominators": "Applications, tenant-local candidates, and interviews are counted separately and never conflated.",
            },
            "cohort_suppression_disclosure": COHORT_SUPPRESSION_DISCLOSURE,
            "governance_disclosure": ANALYTICS_GOVERNANCE_DISCLOSURE,
        }

    @classmethod
    def get_operations_analytics(
        cls,
        uow: UnitOfWork,
        actor: ActorContext,
        job_id: UUID | None = None,
        team_id: UUID | None = None,
        from_utc: datetime | None = None,
        to_utc: datetime | None = None,
        timezone_name: str = "UTC",
    ) -> dict[str, Any]:
        tz = _resolve_timezone(timezone_name)
        now = _ensure_utc(uow.clock.now())
        win_start, win_end = _resolve_window(now, from_utc, to_utc)
        tenant_id, scoped_job_ids = _resolve_scoped_job_ids(uow, actor, job_id, team_id)
        min_cohort, _, _ = _read_org_analytics_policy(uow, tenant_id)

        # 1. Upload intake metrics
        upload_items = list(
            uow.session.scalars(
                select(UploadItem).where(
                    UploadItem.tenant_id == tenant_id,
                    UploadItem.created_at >= win_start,
                    UploadItem.created_at <= win_end,
                )
            ).all()
        )
        upload_counts = {
            "total": len(upload_items),
            "clean_succeeded": sum(
                1 for u in upload_items if u.status == UploadItemStatus.CLEAN.value
            ),
            "quarantined_or_scanning": sum(
                1
                for u in upload_items
                if u.status
                in {
                    UploadItemStatus.RESERVED.value,
                    UploadItemStatus.QUARANTINED.value,
                    UploadItemStatus.SCANNING.value,
                }
            ),
            "blocked_security_or_policy": sum(
                1 for u in upload_items if u.status == UploadItemStatus.BLOCKED.value
            ),
            "failed": sum(1 for u in upload_items if u.status == UploadItemStatus.FAILED.value),
        }

        # 2. Background job_operation queue age & processing latency
        ops = list(
            uow.session.scalars(
                select(JobOperation).where(
                    JobOperation.tenant_id == tenant_id,
                    JobOperation.created_at >= win_start,
                    JobOperation.created_at <= win_end,
                )
            ).all()
        )
        pending_ops = [
            o
            for o in ops
            if o.status in {OperationStatus.PENDING.value, OperationStatus.RETRY_WAIT.value}
        ]
        oldest_pending_age_sec = (
            round(
                max((now - _ensure_utc(o.created_at)).total_seconds() for o in pending_ops),
                3,
            )
            if pending_ops
            else 0.0
        )
        completed_latencies = sorted(
            max(0.0, (_ensure_utc(o.updated_at) - _ensure_utc(o.created_at)).total_seconds())
            for o in ops
            if o.status == OperationStatus.SUCCEEDED.value
        )

        # 3. Evidence extraction & citation validation rejection rates
        extractions = list(
            uow.session.scalars(
                select(ExtractionRun).where(
                    ExtractionRun.tenant_id == tenant_id,
                    ExtractionRun.started_at >= win_start,
                    ExtractionRun.started_at <= win_end,
                )
            ).all()
        )
        summaries = list(
            uow.session.scalars(
                select(CandidateSummaryRevision).where(
                    CandidateSummaryRevision.tenant_id == tenant_id,
                    CandidateSummaryRevision.created_at >= win_start,
                    CandidateSummaryRevision.created_at <= win_end,
                )
            ).all()
        )
        rejected_summaries = sum(1 for s in summaries if s.quality_status == "REJECTED")

        # 4. Screening override rate
        screening_runs = (
            list(
                uow.session.scalars(
                    select(ScreeningRun)
                    .join(
                        Application,
                        sa.and_(
                            Application.tenant_id == ScreeningRun.tenant_id,
                            Application.id == ScreeningRun.application_id,
                        ),
                    )
                    .where(
                        ScreeningRun.tenant_id == tenant_id,
                        Application.job_id.in_(scoped_job_ids),
                        ScreeningRun.created_at >= win_start,
                        ScreeningRun.created_at <= win_end,
                    )
                ).all()
            )
            if scoped_job_ids
            else []
        )
        run_ids = [sr.id for sr in screening_runs]
        total_results = (
            int(
                uow.session.scalar(
                    select(func.count(ScreeningResult.id)).where(
                        ScreeningResult.tenant_id == tenant_id,
                        ScreeningResult.screening_run_id.in_(run_ids),
                    )
                )
                or 0
            )
            if run_ids
            else 0
        )
        override_count = int(
            uow.session.scalar(
                select(func.count(ScreeningOverride.id)).where(
                    ScreeningOverride.tenant_id == tenant_id,
                    ScreeningOverride.created_at >= win_start,
                    ScreeningOverride.created_at <= win_end,
                )
            )
            or 0
        )
        override_suppressed = 0 < total_results < min_cohort

        # 5. Feedback completion & pack publication counts
        published_packs = (
            int(
                uow.session.scalar(
                    select(func.count(PackRevision.id)).where(
                        PackRevision.tenant_id == tenant_id,
                        PackRevision.job_id.in_(scoped_job_ids),
                        PackRevision.status == "PUBLISHED",
                    )
                )
                or 0
            )
            if scoped_job_ids
            else 0
        )
        feedbacks = (
            list(
                uow.session.scalars(
                    select(Feedback).where(
                        Feedback.tenant_id == tenant_id,
                        Feedback.job_id.in_(scoped_job_ids),
                        Feedback.created_at >= win_start,
                        Feedback.created_at <= win_end,
                    )
                ).all()
            )
            if scoped_job_ids
            else []
        )
        submitted_fb = sum(
            1
            for f in feedbacks
            if f.status in {FeedbackStatus.SUBMITTED.value, FeedbackStatus.AMENDED.value}
        )
        fb_suppressed = 0 < len(feedbacks) < min_cohort

        # 6. Reflective scheduling conflict / unknown external-action metrics (P13/P14 seam)
        tables = set(sa_inspect(uow.session.connection()).get_table_names())
        unknown_external_actions = 0
        scheduling_conflicts = 0
        confirmed_interviews = 0
        if "external_action" in tables:
            unknown_external_actions = int(
                uow.session.execute(
                    sa.text(
                        "SELECT COUNT(*) FROM external_action "
                        "WHERE tenant_id = :tid AND status = 'UNKNOWN'"
                    ),
                    {"tid": str(tenant_id)},
                ).scalar()
                or 0
            )
        if "interview" in tables:
            confirmed_interviews = int(
                uow.session.execute(
                    sa.text(
                        "SELECT COUNT(*) FROM interview "
                        "WHERE tenant_id = :tid AND status = 'BOOKED'"
                    ),
                    {"tid": str(tenant_id)},
                ).scalar()
                or 0
            )
            scheduling_conflicts = int(
                uow.session.execute(
                    sa.text(
                        "SELECT COUNT(*) FROM interview "
                        "WHERE tenant_id = :tid AND status = 'RECONCILIATION_REQUIRED'"
                    ),
                    {"tid": str(tenant_id)},
                ).scalar()
                or 0
            )

        return {
            "as_of_utc": now.isoformat(),
            "timezone": timezone_name,
            "window_start_utc": win_start.isoformat(),
            "window_end_utc": win_end.isoformat(),
            "window_start_local": win_start.astimezone(tz).isoformat(),
            "window_end_local": win_end.astimezone(tz).isoformat(),
            "data_freshness": "TRANSACTIONAL_REALTIME",
            "min_cohort_size": min_cohort,
            "uploads": upload_counts,
            "background_operations": {
                "total_in_window": len(ops),
                "succeeded": sum(1 for o in ops if o.status == OperationStatus.SUCCEEDED.value),
                "pending_or_retry_wait": len(pending_ops),
                "needs_review": sum(
                    1 for o in ops if o.status == OperationStatus.NEEDS_REVIEW.value
                ),
                "failed": sum(1 for o in ops if o.status == OperationStatus.FAILED.value),
                "oldest_pending_age_seconds": oldest_pending_age_sec,
                "median_processing_seconds": (
                    round(statistics.median(completed_latencies), 3) if completed_latencies else 0.0
                ),
                "p95_processing_seconds": _percentile(completed_latencies, 0.95),
            },
            "evidence_and_screening": {
                "extraction_runs_total": len(extractions),
                "extraction_incomplete_or_failed": sum(
                    1
                    for e in extractions
                    if e.status != ExtractionRunStatus.SUCCEEDED.value or not e.is_complete
                ),
                "summary_revisions_total": len(summaries),
                "summary_evidence_validation_failures": rejected_summaries,
                "screening_results_evaluated": total_results,
                "human_overrides_recorded": override_count,
                "override_rate_suppressed_small_cohort": override_suppressed,
                "human_override_rate": (
                    None
                    if (total_results == 0 or override_suppressed)
                    else round(override_count / total_results, 4)
                ),
            },
            "scheduling_and_interviews": {
                "scheduling_tables_active": "interview" in tables,
                "confirmed_booked_interviews": confirmed_interviews,
                "reconciliation_or_conflict_count": scheduling_conflicts,
                "unknown_external_actions_count": unknown_external_actions,
                "published_interview_packs": published_packs,
                "feedback_records_total": len(feedbacks),
                "feedback_submitted_count": submitted_fb,
                "feedback_rate_suppressed_small_cohort": fb_suppressed,
                "feedback_completion_rate": (
                    None
                    if (len(feedbacks) == 0 or fb_suppressed)
                    else round(submitted_fb / len(feedbacks), 4)
                ),
            },
            "cohort_suppression_disclosure": COHORT_SUPPRESSION_DISCLOSURE,
            "governance_disclosure": ANALYTICS_GOVERNANCE_DISCLOSURE,
        }

    @classmethod
    def get_organization_usage(
        cls,
        uow: UnitOfWork,
        actor: ActorContext,
        from_utc: datetime | None = None,
        to_utc: datetime | None = None,
    ) -> dict[str, Any]:
        now = _ensure_utc(uow.clock.now())
        win_start, win_end = _resolve_window(now, from_utc, to_utc)
        tenant_id, _ = _resolve_scoped_job_ids(uow, actor)
        _, ai_enabled, monthly_budget = _read_org_analytics_policy(uow, tenant_id)

        counters = list(
            uow.session.scalars(
                select(UsageCounter).where(
                    UsageCounter.tenant_id == tenant_id,
                    UsageCounter.period_date >= win_start.date(),
                    UsageCounter.period_date <= win_end.date(),
                )
            ).all()
        )
        by_model: dict[str, dict[str, int]] = defaultdict(
            lambda: {
                "invocations": 0,
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
            }
        )
        total_prompt = 0
        total_completion = 0
        total_tokens = 0
        total_invocations = 0
        for c in counters:
            key = f"{c.provider_name}:{c.model_id}:{c.usage_category}"
            by_model[key]["invocations"] += int(c.invocation_count)
            by_model[key]["prompt_tokens"] += int(c.prompt_tokens)
            by_model[key]["completion_tokens"] += int(c.completion_tokens)
            by_model[key]["total_tokens"] += int(c.total_tokens)
            total_invocations += int(c.invocation_count)
            total_prompt += int(c.prompt_tokens)
            total_completion += int(c.completion_tokens)
            total_tokens += int(c.total_tokens)

        doc_bytes = int(
            uow.session.scalar(
                select(func.coalesce(func.sum(DocumentVersion.byte_size), 0)).where(
                    DocumentVersion.tenant_id == tenant_id
                )
            )
            or 0
        )
        export_bytes = int(
            uow.session.scalar(
                select(func.coalesce(func.sum(ExportArtifact.byte_size), 0)).where(
                    ExportArtifact.tenant_id == tenant_id,
                    ExportArtifact.status == ExportStatus.READY.value,
                )
            )
            or 0
        )

        return {
            "as_of_utc": now.isoformat(),
            "window_start_utc": win_start.isoformat(),
            "window_end_utc": win_end.isoformat(),
            "ai_processing_enabled": ai_enabled,
            "monthly_ai_token_budget": monthly_budget,
            "budget_remaining_tokens": max(0, monthly_budget - total_tokens),
            "ai_usage": {
                "total_invocations": total_invocations,
                "prompt_tokens": total_prompt,
                "completion_tokens": total_completion,
                "total_tokens": total_tokens,
                "breakdown": [{"bucket": k, **vals} for k, vals in sorted(by_model.items())],
            },
            "storage_usage": {
                "document_versions_bytes": doc_bytes,
                "active_export_artifacts_bytes": export_bytes,
                "total_retained_bytes": doc_bytes + export_bytes,
            },
            "governance_disclosure": ANALYTICS_GOVERNANCE_DISCLOSURE,
        }
