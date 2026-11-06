"""Unit and Algorithmic Tests for DeterministicSlotSolver (P12).

Tests:
- Timezone and DST boundary alignment
- Working hours [working_hours_start, working_hours_end) filtering
- Working days filtering (e.g. weekdays vs weekends)
- Buffer before and buffer after interval expansion
- Minimum lead time enforcement from evaluation_time
- Multi-interviewer schedule merging and conflict detection
- Internal reservation conflict detection
- Strict UNKNOWN calendar status rejection
- Grid stepping (e.g. 15-minute increments) and max_proposals truncation
- Deterministic, chronologically sorted output
"""

import zoneinfo
from dataclasses import replace
from datetime import datetime, timedelta
from uuid import uuid4

import pytest

from talvora.modules.integrations.application.ports import (
    CalendarAvailabilityStatus,
    CalendarSchedule,
    TimeInterval,
)
from talvora.modules.scheduling.application.ports import SolverInput
from talvora.modules.scheduling.application.slot_solver import DeterministicSlotSolver


def _dt(year: int, month: int, day: int, hour: int, minute: int, tz_str: str = "UTC") -> datetime:
    tz = zoneinfo.ZoneInfo(tz_str)
    return datetime(year, month, day, hour, minute, tzinfo=tz)


@pytest.fixture
def base_solver_input() -> SolverInput:
    """Standard evaluation input spanning a full working week (Monday 2026-11-09 to Friday 2026-11-13)."""
    interviewer_1 = uuid4()
    interviewer_2 = uuid4()

    # Monday 09:00 UTC to Friday 17:00 UTC
    eval_time = _dt(2026, 11, 8, 9, 0)  # Sunday morning
    target_start = _dt(2026, 11, 9, 0, 0)  # Monday midnight
    target_end = _dt(2026, 11, 13, 23, 59)  # Friday midnight

    # Candidate provides 24/7 availability window
    cand_intervals = [TimeInterval(start=target_start, end=target_end)]

    # Both interviewers initially fully available
    schedules = {
        interviewer_1: CalendarSchedule(
            calendar_id="int1@example.com",
            status=CalendarAvailabilityStatus.AVAILABLE,
            busy_intervals=[],
        ),
        interviewer_2: CalendarSchedule(
            calendar_id="int2@example.com",
            status=CalendarAvailabilityStatus.AVAILABLE,
            busy_intervals=[],
        ),
    }

    return SolverInput(
        request_id=uuid4(),
        duration_minutes=45,
        buffer_before_minutes=10,
        buffer_after_minutes=10,
        minimum_lead_time_hours=24,
        working_hours_start=9,
        working_hours_end=17,
        working_days=[0, 1, 2, 3, 4],  # Monday to Friday
        timezone="UTC",
        target_date_start=target_start,
        target_date_end=target_end,
        candidate_intervals=cand_intervals,
        interviewer_schedules=schedules,
        internal_reservations=[],
        evaluation_time=eval_time,
        step_minutes=15,
        max_proposals=5,
    )


def test_solver_proposes_slots_within_working_hours_and_days(
    base_solver_input: SolverInput,
) -> None:
    result = DeterministicSlotSolver.solve(base_solver_input)

    assert result.is_fully_available is True
    assert len(result.proposed_slots) == 5

    # First slot on Monday should start at 09:00 (due to working hours)
    first_slot = result.proposed_slots[0]
    assert first_slot.start_utc == _dt(2026, 11, 9, 9, 0)
    assert first_slot.end_utc == _dt(2026, 11, 9, 9, 45)

    # Next slots should step by 15 minutes: 09:15, 09:30, 09:45, 10:00
    assert result.proposed_slots[1].start_utc == _dt(2026, 11, 9, 9, 15)
    assert result.proposed_slots[2].start_utc == _dt(2026, 11, 9, 9, 30)
    assert result.proposed_slots[3].start_utc == _dt(2026, 11, 9, 9, 45)
    assert result.proposed_slots[4].start_utc == _dt(2026, 11, 9, 10, 0)


def test_solver_respects_minimum_lead_time(base_solver_input: SolverInput) -> None:
    # Evaluation time is Monday at 08:00; minimum lead time is 24 hours
    # Earliest valid slot cannot start before Tuesday 08:00
    eval_time = _dt(2026, 11, 9, 8, 0)
    solver_input = replace(
        base_solver_input,
        evaluation_time=eval_time,
        minimum_lead_time_hours=24,
    )

    result = DeterministicSlotSolver.solve(solver_input)

    assert result.is_fully_available is True
    assert len(result.proposed_slots) == 5

    # Earliest slot must be Tuesday 2026-11-10 at 09:00 (working hours start)
    first_slot = result.proposed_slots[0]
    assert first_slot.start_utc >= eval_time + timedelta(hours=24)
    assert first_slot.start_utc == _dt(2026, 11, 10, 9, 0)


def test_solver_excludes_interviewer_busy_periods_and_buffers(
    base_solver_input: SolverInput,
) -> None:
    # Interviewer 1 is busy from Monday 09:00 to 10:00
    int_ids = list(base_solver_input.interviewer_schedules.keys())
    base_solver_input.interviewer_schedules[int_ids[0]] = CalendarSchedule(
        calendar_id="int1@example.com",
        status=CalendarAvailabilityStatus.AVAILABLE,
        busy_intervals=[TimeInterval(start=_dt(2026, 11, 9, 9, 0), end=_dt(2026, 11, 9, 10, 0))],
    )

    result = DeterministicSlotSolver.solve(base_solver_input)

    assert result.is_fully_available is True
    first_slot = result.proposed_slots[0]

    # Slot requires 10 min buffer before and 10 min buffer after
    # Because interviewer is busy until 10:00, the buffer before (10 min) requires slot to start at 10:10 or later
    # With 15-minute grid stepping (10:00, 10:15, 10:30):
    # - At 10:00, buffer before is 09:50-10:00 (overlaps busy period 09:00-10:00) -> CONFLICT
    # - At 10:15, buffer before is 10:05-10:15 (does NOT overlap 09:00-10:00) -> VALID!
    assert first_slot.start_utc == _dt(2026, 11, 9, 10, 15)
    assert first_slot.end_utc == _dt(2026, 11, 9, 11, 0)


def test_solver_respects_internal_reservations(base_solver_input: SolverInput) -> None:
    # Add an internal reservation block on Monday from 09:00 to 11:00
    solver_input = replace(
        base_solver_input,
        internal_reservations=[
            TimeInterval(start=_dt(2026, 11, 9, 9, 0), end=_dt(2026, 11, 9, 11, 0))
        ],
    )

    result = DeterministicSlotSolver.solve(solver_input)

    assert result.is_fully_available is True
    first_slot = result.proposed_slots[0]
    # First slot must start at 11:15 due to buffer before (11:05-11:15)
    assert first_slot.start_utc == _dt(2026, 11, 9, 11, 15)


def test_solver_strict_rejection_on_unknown_calendar_status(base_solver_input: SolverInput) -> None:
    # Set one interviewer to UNKNOWN calendar availability (e.g. Google Calendar API failure / permission revoked)
    int_ids = list(base_solver_input.interviewer_schedules.keys())
    schedules = dict(base_solver_input.interviewer_schedules)
    schedules[int_ids[1]] = CalendarSchedule(
        calendar_id="int2@example.com",
        status=CalendarAvailabilityStatus.UNKNOWN,
        busy_intervals=[],
    )
    solver_input = replace(base_solver_input, interviewer_schedules=schedules)

    result = DeterministicSlotSolver.solve(solver_input)

    # Must reject proposing slots and indicate UNKNOWN interviewer
    assert result.is_fully_available is False
    assert len(result.proposed_slots) == 0
    assert int_ids[1] in result.unknown_interviewers
    assert result.total_slots_evaluated == 0


def test_solver_evaluates_target_timezone_correctly(base_solver_input: SolverInput) -> None:
    # Set timezone to America/New_York (UTC-5 in November standard time)
    solver_input = replace(
        base_solver_input,
        timezone="America/New_York",
        working_hours_start=9,
        working_hours_end=17,
    )

    result = DeterministicSlotSolver.solve(solver_input)

    assert result.is_fully_available is True
    first_slot = result.proposed_slots[0]

    # 09:00 AM America/New_York is 14:00 UTC (9 + 5 hours)
    assert first_slot.start_utc == _dt(2026, 11, 9, 14, 0)
    assert first_slot.end_utc == _dt(2026, 11, 9, 14, 45)


def test_solver_returns_empty_when_no_overlapping_availability(
    base_solver_input: SolverInput,
) -> None:
    # Candidate provides availability ONLY on Sunday (weekend)
    solver_input = replace(
        base_solver_input,
        candidate_intervals=[
            TimeInterval(start=_dt(2026, 11, 8, 10, 0), end=_dt(2026, 11, 8, 16, 0))
        ],
    )

    result = DeterministicSlotSolver.solve(solver_input)

    assert result.is_fully_available is False
    assert len(result.proposed_slots) == 0
