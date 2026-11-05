# ADR-0007: Durable Outbox, Job Ledger, and SQS Transport

## Status
Accepted

## Context
Background tasks (resume malware scanning, text extraction, OCR, AI profile generation, email dispatch, and calendar booking) must survive API process crashes, queue broker outages, and network partitions. Naive in-process background tasks (such as FastAPI `BackgroundTasks`) lose state on worker restart. Furthermore, directly publishing to a message broker within an active database transaction risks dual-write inconsistencies if the broker fails or the transaction rolls back.

## Decision
Talvora implements task durability using the **Transactional Outbox Pattern** with a **PostgreSQL Job Ledger** and **Amazon SQS Standard** transport:

1. **Transactional Outbox Persistence:**
   - In a single atomic PostgreSQL transaction, the API persists:
     - Domain entity mutations.
     - An audit log event.
     - A `job_operation` row (status: `PENDING`).
     - An `outbox_event` row (status: `PENDING`, topic, reference ID).
   - The API immediately returns HTTP `202 Accepted` with an `operation_id` and `status_url` (`/api/v1/operations/{id}`).
2. **Outbox Coordinator Publisher:**
   - A lightweight coordinator process sweeps the `outbox_event` table every 500 ms for unprocessed events.
   - It publishes an SQS message containing minimal metadata:
     `{"tenant_id": "<uuid>", "job_id": "<uuid>", "operation_id": "<uuid>", "correlation_id": "<uuid>"}`.
   - Upon successful SQS delivery, the outbox row is marked `PUBLISHED`.
3. **Lease and Fencing Token Model:**
   - Celery workers consume SQS messages. Before processing, a worker executes `SELECT ... FOR UPDATE` on `job_operation`, obtaining an exclusive lease and an incremented `fencing_token`.
   - Long-running tasks send periodic heartbeats to extend the lease.
   - If a worker crashes or hangs, the lease expires. The coordinator recovers the expired job and re-queues it.
   - A worker attempting to commit with an outdated fencing token is rejected by the database.
4. **PostgreSQL-Managed Backoff & Retries:**
   - Retry counts and `next_attempt_at` timestamps live in `job_operation`, not in broker-level countdowns.
   - Maximum transient retries: 5 attempts with jittered exponential backoff.
   - Ambiguous side effects enter `UNKNOWN` state for reconciliation.

## Consequences
- **Positive:**
  - Guaranteed at-least-once task admission without dual-write race conditions.
  - SQS broker outages do not lose committed work; tasks remain durable in PostgreSQL until the queue recovers.
  - Zero sensitive payloads in queue broker logs.
- **Negative / Trade-offs:**
  - Additional database write per background task.
  - At-least-once delivery requires worker handlers to be strictly idempotent.
