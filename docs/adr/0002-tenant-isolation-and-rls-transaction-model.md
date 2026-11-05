# ADR-0002: Tenant Isolation, Row-Level Security (RLS), and Context Handling

## Status
Accepted

## Context
Talvora is an enterprise multi-tenant B2B platform handling confidential candidate resumes, compensation expectations, job criteria, and internal interviewer feedback. A cross-tenant data leak represents a fatal security and regulatory failure. Application-level filtering alone (e.g., adding `WHERE tenant_id = ...` manually in every query) is prone to human error, regression in complex joins, and developer omission.

## Decision
Talvora enforces multi-tenant data isolation at the relational database engine level using **PostgreSQL Row-Level Security (RLS)** combined with explicit, transaction-local context setting:

1. **Schema Invariants:**
   - Every tenant-owned table includes a non-null `tenant_id UUID`.
   - Primary and composite foreign keys explicitly bind `tenant_id` (e.g., `FOREIGN KEY (tenant_id, candidate_id) REFERENCES candidates(tenant_id, id)`).
   - Indexes begin with `tenant_id`.
2. **PostgreSQL RLS Enforcement:**
   - Every tenant table enables and forces RLS: `ALTER TABLE {table} ENABLE ROW LEVEL SECURITY; ALTER TABLE {table} FORCE ROW LEVEL SECURITY;`.
   - Policies enforce both read (`USING`) and write (`WITH CHECK`):
     ```sql
     CREATE POLICY tenant_isolation_policy ON {table}
       AS RESTRICTIVE
       USING (tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid)
       WITH CHECK (tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid);
     ```
3. **Transaction-Local Context Injection:**
   - Tenant context is set at the start of **each database transaction** on the active connection:
     ```python
     session.execute(text("SELECT set_config('app.current_tenant_id', :tenant_id, true)"), {"tenant_id": str(tenant_id)})
     ```
   - Parameter `true` ensures the setting is local to the current transaction only and automatically clears upon transaction `COMMIT` or `ROLLBACK`.
4. **Unprivileged Runtime Role:**
   - Application and Celery processes connect as an unprivileged PostgreSQL user (`talvora_app`) possessing `NOBYPASSRLS`. Only migration scripts execute as the schema owner.
5. **Concealment Semantics:**
   - Queries attempting to read an entity ID belonging to another tenant return `404 Not Found` rather than `403 Forbidden` to prevent resource enumeration.

## Consequences
- **Positive:**
  - Database-enforced isolation: an omitted `WHERE` clause in code cannot leak records belonging to another tenant.
  - Connection pooling works safely because context is strictly transaction-local.
- **Negative / Trade-offs:**
  - Developers must ensure every transaction sets context before executing queries.
  - Integration tests must run under the unprivileged runtime role to accurately verify policy enforcement.
