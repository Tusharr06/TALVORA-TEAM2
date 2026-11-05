# ADR-0008: Google OAuth Scope Strategy and Integration Adapters

## Status
Accepted

## Context
Talvora automates interview scheduling by querying interviewer availability, inserting confirmed calendar events, and dispatching recruiter-reviewed invitation emails. Requesting broad Google Workspace permissions (such as full Gmail read/write or unrestricted Calendar management) creates severe security, compliance, and enterprise trust liabilities. Furthermore, Google OAuth requires explicit user consent, strict token handling, and clear separation from staff login credentials.

## Decision
Talvora implements a **Least-Privilege Google OAuth Strategy** with envelope encryption and strict scope minimization:

1. **Scope Boundaries:**
   - **Interviewer Availability (Panelists):**
     - Requested Scope: `https://www.googleapis.com/auth/calendar.events.freebusy`
     - Read-only free/busy schedule access. Calendar event details, titles, and attendee lists are completely unreadable.
   - **Organizer Event Management (Booking Lead):**
     - Requested Scope: `https://www.googleapis.com/auth/calendar.events.owned` (or narrowest functional event insertion scope).
     - Allows creating and managing only events organized by Talvora.
   - **Recruiter Email Dispatch (Candidate Communications):**
     - Requested Scope: `https://www.googleapis.com/auth/gmail.send`
     - Send-only permission. The recruiter's inbox is **never readable or indexable**.
2. **Separation from Identity Login:**
   - Google integration OAuth grants are completely separated from user authentication (Cognito).
   - An organization member connects their Google account via a dedicated flow (`POST /api/v1/integrations/google/authorize`).
   - The integration record is bound strictly to `(tenant_id, user_id)`.
3. **Envelope Encryption for OAuth Tokens:**
   - Refresh tokens and access tokens are never stored in plaintext.
   - Tokens are encrypted server-side using AWS KMS (or local AES-GCM in development) using unique data keys per organization.
   - Ciphertext, Key ID, and encryption version are persisted in `integration_connection`.
   - Tokens are never exposed in API responses or logs.
4. **Token Refresh Serialization:**
   - A distributed database row lock (`SELECT ... FOR UPDATE`) prevents concurrent refresh races when multiple workers require valid access tokens simultaneously.

## Consequences
- **Positive:**
  - Minimal attack surface; zero mailbox reading capability.
  - Transparent user consent matching exact functional requirements.
- **Negative / Trade-offs:**
  - Send-only Gmail access cannot inspect the sent folder to confirm delivery after a network timeout (addressed via ADR-0009).
  - External OAuth consent screens require Google verification for production rollout.
