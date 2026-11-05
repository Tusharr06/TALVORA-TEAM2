# ADR-0003: Authentication, Session Management, and CSRF Protection

## Status
Accepted

## Context
Talvora serves two distinct user populations:
1. Internal staff users (Owners, Admins, Recruiters, Hiring Managers, Interviewers) requiring enterprise Single Sign-On (SSO) and persistent sessions.
2. External candidates accessing self-scheduling portals via capability links.

Storing JWTs or OAuth tokens in client-side storage (e.g., `localStorage`) leaves credentials vulnerable to Cross-Site Scripting (XSS) extraction. Furthermore, custom password hashing systems introduce substantial regulatory, auditing, and credential management burdens.

## Decision
Talvora delegates primary identity verification to **AWS Cognito via OpenID Connect (OIDC)** (with a local Keycloak/mock profile for development) and manages application access via **server-side opaque sessions**:

1. **OIDC Authorization Code Flow with PKCE:**
   - Staff login executes OIDC authorization code grant with Proof Key for Code Exchange (PKCE), state verification, and cryptographic nonces.
   - User identity is mapped strictly via `(issuer, subject)`. Email addresses are not immutable identity keys.
2. **Server-Managed Opaque Sessions:**
   - Successful authentication writes a session record to PostgreSQL (`auth_session`), hashing the session token using SHA-256.
   - The raw session token is issued to the browser in an `__Host-` prefixed cookie:
     - Name: `__Host-talvora_session`
     - Flags: `Secure; HttpOnly; SameSite=Lax; Path=/`
   - Inactivity timeout: 30 minutes; absolute maximum lifetime: 8 hours.
3. **Cross-Site Request Forgery (CSRF) Protection:**
   - State-changing API mutations (`POST`, `PUT`, `PATCH`, `DELETE`) require an anti-CSRF token delivered via an `X-CSRF-Token` header and verified against the session's active secret.
   - Requests verify `Origin` and `Referer` headers against the approved application origin.
4. **Candidate Capability Link Isolation:**
   - Candidate scheduling links contain high-entropy 256-bit secrets.
   - Submitting the link token exchanges it for an isolated, short-lived candidate session cookie (`__Host-talvora_candidate_session`) scoped strictly to that single interview request.
   - Staff sessions cannot access candidate endpoints, and candidate sessions cannot access internal staff workspaces.

## Consequences
- **Positive:**
  - Browser storage is free of access tokens, eliminating token theft via XSS.
  - Revocation is instantaneous server-side by deleting the session row in PostgreSQL.
- **Negative / Trade-offs:**
  - Every API request incurs a fast database lookup for session validity (mitigated by indexed lookup on session hash).
  - Cross-origin API calls require reverse proxy configuration; single-origin architecture is mandatory.
