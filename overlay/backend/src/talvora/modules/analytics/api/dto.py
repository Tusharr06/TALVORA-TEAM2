"""Typed Pydantic v2 Response DTOs for Pipeline, Operations, and Usage Analytics (P17)."""

from typing import Any

from pydantic import BaseModel


class PipelineDenominatorsResponse(BaseModel):
    distinct_applications_count: int
    distinct_candidates_count: int
    distinct_interviews_count: int


class StageAnalyticsItemResponse(BaseModel):
    stage: str
    current_applications_count: int
    stage_entries_in_window: int
    stage_exits_in_window: int
    dwell_sample_size: int
    suppressed_small_cohort: bool
    median_time_in_stage_hours: float | None
    p90_time_in_stage_hours: float | None


class PipelineAnalyticsResponse(BaseModel):
    as_of_utc: str
    timezone: str
    window_start_utc: str
    window_end_utc: str
    window_start_local: str
    window_end_local: str
    data_freshness: str
    min_cohort_size: int
    scoped_job_count: int
    denominators: PipelineDenominatorsResponse
    stages: list[StageAnalyticsItemResponse]
    metric_definitions: dict[str, str]
    cohort_suppression_disclosure: str
    governance_disclosure: str


class OperationsAnalyticsResponse(BaseModel):
    as_of_utc: str
    timezone: str
    window_start_utc: str
    window_end_utc: str
    window_start_local: str
    window_end_local: str
    data_freshness: str
    min_cohort_size: int
    uploads: dict[str, int]
    background_operations: dict[str, Any]
    evidence_and_screening: dict[str, Any]
    scheduling_and_interviews: dict[str, Any]
    cohort_suppression_disclosure: str
    governance_disclosure: str


class OrganizationUsageResponse(BaseModel):
    as_of_utc: str
    window_start_utc: str
    window_end_utc: str
    ai_processing_enabled: bool
    monthly_ai_token_budget: int
    budget_remaining_tokens: int
    ai_usage: dict[str, Any]
    storage_usage: dict[str, int]
    governance_disclosure: str
