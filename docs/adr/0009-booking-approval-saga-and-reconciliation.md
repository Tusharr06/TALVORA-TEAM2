# ADR-0009: Booking Approval Saga and External Side Effect Reconciliation

## Status
Accepted

## Context
Executing external mutations (inserting Google Calendar events, sending candidate emails via Gmail) cannot participate in relational ACID transactions. Network timeouts, external API rate limits, or worker restarts can occur while an external write is in-flight. If a system blindly retries after a timeout, it risks sending duplicate invitations or creating duplicate calendar bookings. Conversely, if it ignores timeouts, it may cause phantom bookings where the calendar event exists externally but is lost locally.

## Decision
Talvora governs all external side effects via an **Approval-Gated Saga with Idempotent Reconciliation**:

1. **Mandatory Human Approval Gate:**
   - Every external action (booking, cancellation, email dispatch) begins as a `DRAFT` payload.
   - An authorized recruiter reviews and signs the exact action payload.
   - The approval stores an HMAC-SHA256 digest of the canonical payload, binding:
     - Tenant ID, Actor ID, Target interview request ID.
     - Scheduled UTC start and end times.
     - Exact attendee list and organizer connection ID.
     - Approved message text and notification flags.
   - Expiration window: 15 minutes for calendar bookings; 24 hours for availability request emails. Any payload modification invalidates the approval.
2. **Deterministic Provider IDs:**
   - Google Calendar event IDs are generated deterministically prior to the API call:
     $$\text{Event ID} = \text{cal\_evt\_} \parallel \text{external\_action\_uuid}$$
   - If an API call times out, the worker can query Google Calendar for that exact ID to verify if the event was successfully created.
3. **Database Range Exclusion Locking:**
   - Internal interviewer schedules are protected by PostgreSQL `tstzrange` and `btree_gist` exclusion constraints.
   - An active booking reserves the interviewer's interval inside a database transaction before calling Google, preventing concurrent internal double-booking.
4. **Tri-State Execution and Reconciliation:**
   - Pre-call failure: Transitions to `RETRYABLE` (safe exponential backoff).
   - In-flight timeout / HTTP 5xx: Transitions to `UNKNOWN`.
   - The outbox coordinator schedules a reconciliation task to inspect the provider using the deterministic ID:
     - Event found with matching Talvora payload hash: Transition to `SUCCEEDED`.
     - Event definitely absent: Transition to `FAILED` or safe retry.
     - Ambiguous send-only Gmail timeouts: UI alerts the recruiter to manually confirm whether the candidate received the message, preventing automated duplicate spam.

## Consequences
- **Positive:**
  - Zero double-booking within internal tenant interviewers.
  - Provable crash-recovery without phantom or duplicate calendar events.
- **Negative / Trade-offs:**
  - Additional operational complexity for managing `UNKNOWN` reconciliation workflows.
