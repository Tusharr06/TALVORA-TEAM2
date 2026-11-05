# Talvora — Intelligent Talent Operations Workspace

Talvora transforms unstructured candidate resumes, vacancy requirements, interviewer calendar availability, and feedback into inspectable, evidence-backed recruitment work products.

---

## 1. Quickstart & Local Development

### Prerequisites
- Python 3.13+
- [uv](https://docs.astral.sh/uv/) package manager
- Docker and Docker Compose (for PostgreSQL 17, Moto, Mailpit, ClamAV)

### Setup Steps
```bash
# 1. Clone repository and navigate to backend
cd backend

# 2. Create environment file from example baseline
cp .env.example .env

# 3. Create virtual environment and install locked dependencies
uv venv
uv pip install -e ".[dev]"

# 4. Start local infrastructure containers (PostgreSQL, Moto, Mailpit, ClamAV)
docker compose -f ../infra/compose/docker-compose.yml up -d

# 5. Run backend development server
uv run uvicorn talvora.main:app --reload --port 8000
```

Verify service liveness:
```bash
curl http://localhost:8000/health/live
curl http://localhost:8000/health/ready
```

---

## 2. Named Engineering Commands

Run tasks from the repository root:

| Command | Action |
| --- | --- |
| `python scripts/dev.py check` | Execute full verification pipeline (lint, typecheck, unit, integration) |
| `python scripts/dev.py lint` | Execute Ruff format and lint checks |
| `python scripts/dev.py typecheck` | Execute Mypy in strict mode across `src/` and `tests/` |
| `python scripts/dev.py unit` | Run domain unit tests |
| `python scripts/dev.py integration` | Run integration tests (lifecycle, health, database) |
| `python scripts/dev.py openapi` | Export OpenAPI 3.1 schema to `contracts/openapi.json` |
| `python scripts/dev.py dev` | Start development server with hot reloading |

---

## 3. Configuration & Environment Matrix

| Setting / Variable | Local Dev (`local`) | Unit/Integration (`test`) | Staging (`staging`) | Production (`production`) |
| --- | --- | --- | --- | --- |
| `TALVORA_ENVIRONMENT` | `local` | `test` | `staging` | `production` |
| `TALVORA_DEBUG` | `true` | `false` | `false` | `false` (Enforced) |
| `TALVORA_COOKIE_SECURE` | `false` | `false` | `true` (Enforced) | `true` (Enforced) |
| `TALVORA_ALLOW_DEV_IDENTITY_ADAPTER`| `true` | `true` | `false` (Enforced) | `false` (Enforced) |
| `TALVORA_MOTO_ENDPOINT_URL` | `http://localhost:5000` | Mocked in tests | Disabled (None) | Disabled (None) |
| `TALVORA_INTERNAL_DIAGNOSTIC_ENABLED` | `true` | `true` | `false` (Enforced) | `false` (Enforced) |
| `Database Target` | Docker PostgreSQL 17 | In-memory / Isolated DB | AWS RDS PostgreSQL 17 | AWS RDS PostgreSQL 17 Multi-AZ |
| `Object Storage Target` | Local Moto S3 | Moto test doubles | Private AWS S3 (KMS) | Private AWS S3 (KMS) |
| `Messaging Target` | Local Moto SQS | Local test mocks | Amazon SQS Standard | Amazon SQS Standard + DLQ |
| `Mail Dispatch Target` | Local Mailpit SMTP | Captured in tests | Amazon SES / OAuth | Amazon SES / Delegated Google OAuth |

---

## 4. Troubleshooting

- **Database Connection Refused:**
  Ensure Docker is running and verify PostgreSQL container health:
  ```bash
  docker ps --filter "name=talvora-postgres"
  docker logs talvora-postgres
  ```
- **Port Collisions:**
  If ports 5432 (Postgres), 5000 (Moto), or 8025 (Mailpit) are already allocated by host services, modify `.env` port bindings.
- **Strict Settings Rejection:**
  If the application fails startup with `ValueError`, check your `.env` configuration. Setting `TALVORA_ENVIRONMENT=production` automatically blocks local development placeholders, debug flags, and Moto endpoints.
