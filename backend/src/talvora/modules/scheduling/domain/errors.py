"""RFC 7807 Problem Exceptions for Interview Scheduling Subsystem."""

from talvora.shared.transport.problems import ProblemException


class SchedulingError(ProblemException):
    """Base exception for scheduling subsystem."""

    def __init__(
        self,
        status_code: int = 400,
        title: str = "Scheduling Error",
        code: str = "SCHEDULING_ERROR",
        detail: str = "An error occurred during interview scheduling.",
    ) -> None:
        super().__init__(
            status_code=status_code,
            title=title,
            code=code,
            detail=detail,
        )


class CandidateContactNotVerifiedError(SchedulingError):
    def __init__(
        self,
        detail: str = "Candidate contact information must be verified by a human before scheduling interviews.",
    ) -> None:
        super().__init__(
            status_code=422,
            title="Candidate Contact Not Verified",
            code="CANDIDATE_CONTACT_NOT_VERIFIED",
            detail=detail,
        )


class CandidateNotEligibleForInterviewError(SchedulingError):
    def __init__(
        self,
        detail: str = "Application must be transitioned to INTERVIEW_APPROVED before an interview can be scheduled.",
    ) -> None:
        super().__init__(
            status_code=422,
            title="Application Not Eligible for Interview",
            code="APPLICATION_NOT_INTERVIEW_APPROVED",
            detail=detail,
        )


class CandidateLinkExpiredError(SchedulingError):
    def __init__(self, detail: str = "Candidate availability link has expired.") -> None:
        super().__init__(
            status_code=410,
            title="Availability Link Expired",
            code="AVAILABILITY_LINK_EXPIRED",
            detail=detail,
        )


class CandidateLinkInvalidError(SchedulingError):
    def __init__(
        self, detail: str = "Candidate availability link is invalid or unrecognized."
    ) -> None:
        super().__init__(
            status_code=404,
            title="Availability Link Invalid",
            code="AVAILABILITY_LINK_INVALID",
            detail=detail,
        )


class SlotProposalInvalidatedError(SchedulingError):
    def __init__(
        self,
        detail: str = "The proposed interview slot is no longer valid due to schedule changes.",
    ) -> None:
        super().__init__(
            status_code=409,
            title="Proposal Invalidated",
            code="PROPOSAL_INVALIDATED",
            detail=detail,
        )


class NoAvailableSlotsFoundError(SchedulingError):
    def __init__(
        self,
        detail: str = "No overlapping available slots found matching all constraints and working hours.",
    ) -> None:
        super().__init__(
            status_code=404,
            title="No Slots Found",
            code="NO_SLOTS_AVAILABLE",
            detail=detail,
        )


class ExternalActionNotFoundError(SchedulingError):
    def __init__(self, detail: str = "External action was not found.") -> None:
        super().__init__(
            status_code=404,
            title="External Action Not Found",
            code="NOT_FOUND",
            detail=detail,
        )


class ApprovalExpiredError(SchedulingError):
    def __init__(self, detail: str = "Approval has expired or validity window lapsed.") -> None:
        super().__init__(
            status_code=409,
            title="Approval Expired",
            code="APPROVAL_EXPIRED",
            detail=detail,
        )


class ApprovalPayloadChangedError(SchedulingError):
    def __init__(
        self,
        detail: str = "Action parameters or schedule were modified after approval signature.",
    ) -> None:
        super().__init__(
            status_code=409,
            title="Approval Payload Changed",
            code="APPROVAL_PAYLOAD_CHANGED",
            detail=detail,
        )


class SlotConflictError(SchedulingError):
    def __init__(
        self,
        detail: str = "Target calendar interval was booked concurrently or conflicts with existing reservations.",
    ) -> None:
        super().__init__(
            status_code=409,
            title="Slot Conflict",
            code="SLOT_CONFLICT",
            detail=detail,
        )


class AvailabilityUnknownError(SchedulingError):
    def __init__(
        self,
        detail: str = "One or more required interviewer calendars could not be queried or returned UNKNOWN.",
    ) -> None:
        super().__init__(
            status_code=409,
            title="Availability Unknown",
            code="AVAILABILITY_UNKNOWN",
            detail=detail,
        )


class ProviderResultUnknownError(SchedulingError):
    def __init__(
        self,
        detail: str = "Ambiguous provider outcome: HTTP timeout or network error occurred during external write.",
    ) -> None:
        super().__init__(
            status_code=504,
            title="Provider Result Unknown",
            code="PROVIDER_RESULT_UNKNOWN",
            detail=detail,
        )


class InterviewNotFoundError(SchedulingError):
    def __init__(self, detail: str = "Interview was not found.") -> None:
        super().__init__(
            status_code=404,
            title="Interview Not Found",
            code="NOT_FOUND",
            detail=detail,
        )


class InvalidInterviewTransitionError(SchedulingError):
    def __init__(
        self,
        detail: str = "Invalid interview lifecycle state transition.",
    ) -> None:
        super().__init__(
            status_code=409,
            title="Invalid Interview Transition",
            code="INVALID_TRANSITION",
            detail=detail,
        )


class DuplicateActiveInterviewError(SchedulingError):
    def __init__(
        self,
        detail: str = "An active interview already exists for this interview request.",
    ) -> None:
        super().__init__(
            status_code=409,
            title="Duplicate Active Interview",
            code="DUPLICATE_ACTIVE_INTERVIEW",
            detail=detail,
        )


class ReconciliationRequiredError(SchedulingError):
    def __init__(
        self,
        detail: str = "Interview requires manual reconciliation due to external calendar modifications or drift.",
    ) -> None:
        super().__init__(
            status_code=409,
            title="Reconciliation Required",
            code="RECONCILIATION_REQUIRED",
            detail=detail,
        )
