# ADR-0012: Frontend Technology and Phase Boundary

## Status
Accepted

## Context
Talvora is an enterprise recruiter operations workspace requiring interactive, data-dense interfaces: two-pane candidate evidence inspection, side-by-side screening reviews, calendar slot selection, and structured interview feedback forms. The engineering team considered whether to implement the frontend concurrently with backend modules (P01–P18) or defer frontend development until the backend API contracts are fully hardened and frozen.

Furthermore, framework choices between Next.js (SSR/Server Actions) and a Single-Page Application (SPA) with Vite were evaluated.

## Decision
Talvora establishes a **Strict Phase Boundary** deferring all frontend development until Phase P19, utilizing a **React 19 + TypeScript + Vite Single-Page Application (SPA)**:

1. **Strict Phase Ordering (Backend-First):**
   - Phases P00 through P18 are strictly backend, database, integration, and security phases.
   - Zero frontend files (`package.json`, Node dependencies, Vite configs, or UI components) may be scaffolded or committed before P19.
   - P18 concludes with a frozen, exported OpenAPI 3.1 specification (`contracts/openapi.json`).
2. **Frontend Technology Stack (P19+):**
   - **Framework:** React 19, TypeScript (strict mode), Vite.
   - **Routing:** React Router v7 (client-side routing). Single-origin deployment via reverse proxy avoids CORS complexity.
   - **Server State & Caching:** TanStack Query (React Query) manages all server-synchronized data. No global Redux-style store is used.
   - **UI Primitives:** Tailwind CSS with accessible Radix UI primitives and shadcn/ui components meeting WCAG 2.2 AA accessibility standards.
   - **Forms & Validation:** React Hook Form with Zod schemas matching backend constraints.
   - **API Client:** Auto-generated TypeScript client generated directly from the frozen OpenAPI specification (`openapi-typescript-codegen` or `@hey-api/openapi-ts`). Client interfaces are never hand-edited.
3. **Dedicated Candidate Portal Surface:**
   - The candidate self-scheduling interface resides under an isolated routing and bundle boundary (`frontend/src/public-candidate/`) with no shared state or session leakage from the recruiter workspace.

## Consequences
- **Positive:**
  - Prevents churn: UI is built against stable, verified, and frozen backend API contracts.
  - Zero hydration mismatches or duplicated Node backend runtimes (no Next.js server duplication).
  - Clean separation of client-side presentation and server-side authorization.
- **Negative / Trade-offs:**
  - Initial human testing of backend phases (P01–P18) must occur via automated integration tests, HTTPX fixtures, or API clients (e.g., OpenAPI Swagger UI) rather than a graphical browser interface.
