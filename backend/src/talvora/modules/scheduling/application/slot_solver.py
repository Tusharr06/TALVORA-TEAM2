"""Deterministic Interview Slot Solver with Timezone, DST, and Half-Open Interval Algebra."""

import zoneinfo
from datetime import UTC, datetime, timedelta
from uuid import UUID

from talvora.modules.integrations.application.ports import (
    CalendarAvailabilityStatus,
    TimeInterval,
)
from talvora.modules.scheduling.application.ports import (
    CandidateSlot,
    SolverInput,
    SolverResult,
)


def _ensure_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


class DeterministicSlotSolver:
    """Pure algorithmic solver calculating conflict-free interview proposals."""

    @classmethod
    def solve(cls, params: SolverInput) -> SolverResult:
        now_utc = _ensure_utc(params.evaluation_time or datetime.now(UTC))
        min_start_utc = now_utc + timedelta(hours=params.minimum_lead_time_hours)

        # 1. Resolve Timezone
        try:
            target_tz = zoneinfo.ZoneInfo(params.timezone)
        except Exception:
            target_tz = zoneinfo.ZoneInfo("UTC")

        # 2. Check for interviewers with UNKNOWN calendar status
        unknown_interviewers: list[UUID] = []
        for mem_id, sched in params.interviewer_schedules.items():
            if sched.status == CalendarAvailabilityStatus.UNKNOWN:
                unknown_interviewers.append(mem_id)

        # If any interviewer's calendar status is UNKNOWN, we cannot safely propose slots for them
        if unknown_interviewers:
            return SolverResult(
                proposed_slots=[],
                unknown_interviewers=unknown_interviewers,
                total_slots_evaluated=0,
                is_fully_available=False,
            )

        # 3. Aggregate all interviewer busy intervals and internal reservations
        all_interviewer_busy: list[TimeInterval] = []
        for sched in params.interviewer_schedules.values():
            for interval in sched.busy_intervals:
                all_interviewer_busy.append(
                    TimeInterval(start=_ensure_utc(interval.start), end=_ensure_utc(interval.end))
                )

        for res in params.internal_reservations:
            all_interviewer_busy.append(
                TimeInterval(start=_ensure_utc(res.start), end=_ensure_utc(res.end))
            )

        duration = timedelta(minutes=params.duration_minutes)
        buf_before = timedelta(minutes=params.buffer_before_minutes)
        buf_after = timedelta(minutes=params.buffer_after_minutes)
        step = timedelta(minutes=params.step_minutes)

        target_start = max(_ensure_utc(params.target_date_start), min_start_utc)
        target_end = _ensure_utc(params.target_date_end)

        candidate_intervals_utc: list[TimeInterval] = []
        for ci in params.candidate_intervals:
            s = _ensure_utc(ci.start)
            e = _ensure_utc(ci.end)
            if s < e:
                candidate_intervals_utc.append(TimeInterval(start=s, end=e))

        candidate_intervals_utc.sort(key=lambda i: i.start)

        proposed_slots: list[CandidateSlot] = []
        interviewer_ids = list(params.interviewer_schedules.keys())
        total_evaluated = 0

        # 4. Iterate across each candidate availability window
        for cand_win in candidate_intervals_utc:
            # Clamp window to overall target window
            win_start = max(cand_win.start, target_start)
            win_end = min(cand_win.end, target_end)

            if win_start >= win_end:
                continue

            # Align to grid step (ceiling of win_start to step boundary)
            minute = win_start.minute
            remainder = minute % params.step_minutes
            if remainder != 0:
                current_slot_start = win_start + timedelta(
                    minutes=(params.step_minutes - remainder)
                )
            else:
                current_slot_start = win_start.replace(second=0, microsecond=0)

            while current_slot_start + duration <= win_end:
                current_slot_end = current_slot_start + duration
                total_evaluated += 1

                # Working hours and working days check in target timezone
                local_start = current_slot_start.astimezone(target_tz)
                local_end = current_slot_end.astimezone(target_tz)

                if (
                    local_start.weekday() in params.working_days
                    and local_end.weekday() in params.working_days
                    and local_start.date() == local_end.date()
                ):
                    start_minute = local_start.hour * 60 + local_start.minute
                    end_minute = local_end.hour * 60 + local_end.minute
                    wh_start_minute = params.working_hours_start * 60
                    wh_end_minute = params.working_hours_end * 60

                    if start_minute >= wh_start_minute and end_minute <= wh_end_minute:
                        # Full block needed by interviewers (including buffer times)
                        interviewer_block = TimeInterval(
                            start=current_slot_start - buf_before,
                            end=current_slot_end + buf_after,
                        )

                        # Conflict check against interviewer schedules
                        has_conflict = False
                        for busy in all_interviewer_busy:
                            if interviewer_block.overlaps(busy):
                                has_conflict = True
                                break

                        if not has_conflict:
                            # Slot is valid!
                            proposed_slots.append(
                                CandidateSlot(
                                    start_utc=current_slot_start,
                                    end_utc=current_slot_end,
                                    interviewer_membership_ids=interviewer_ids,
                                )
                            )
                            if len(proposed_slots) >= params.max_proposals:
                                break

                current_slot_start += step

            if len(proposed_slots) >= params.max_proposals:
                break

        return SolverResult(
            proposed_slots=proposed_slots,
            unknown_interviewers=[],
            total_slots_evaluated=total_evaluated,
            is_fully_available=len(proposed_slots) > 0,
        )
