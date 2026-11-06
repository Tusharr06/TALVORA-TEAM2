"""Phase P18 Backend Release Gate — Static, Contract, Migration & Determinism Suite."""

import json
from collections import Counter
from pathlib import Path
from typing import cast
from unittest.mock import Mock
from uuid import UUID, uuid4

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory

from talvora.bootstrap import create_app
from talvora.config import Settings
from talvora.modules.facts.domain.models import (
    CandidateFactPolarity,
    CandidateFactRevision,
    CandidateFactStatus,
)
from talvora.modules.jobs.domain.models import Criterion, CriterionType
from talvora.modules.screening.application.service import ScreeningService
from talvora.modules.screening.domain.models import ScreeningOutcome
from talvora.shared.infrastructure.uow import UnitOfWork

REPO_ROOT = Path(__file__).resolve().parents[3]
BACKEND_ROOT = REPO_ROOT / "backend"
OPENAPI_PATH = REPO_ROOT / "contracts" / "openapi.json"


@pytest.mark.unit
def test_frozen_openapi_contract_has_zero_drift_unique_operation_ids_and_problem_schemas() -> None:
    assert OPENAPI_PATH.exists(), "Frozen contracts/openapi.json must exist"
    disk_raw = OPENAPI_PATH.read_text(encoding="utf-8")
    disk_spec = json.loads(disk_raw)

    app = create_app(Settings(environment="local", debug=False))
    live_spec = app.openapi()
    live_raw = json.dumps(live_spec, indent=2, sort_keys=True) + "\n"

    assert disk_raw == live_raw, (
        "contracts/openapi.json drifted from live FastAPI schema; "
        "run `python scripts/export_openapi.py` to synchronize."
    )

    components = disk_spec.get("components", {})
    schemas = components.get("schemas", {})
    assert "ProblemDetail" in schemas
    assert "InvalidParam" in schemas

    sec_schemes = components.get("securitySchemes", {})
    assert "sessionCookie" in sec_schemes
    assert sec_schemes["sessionCookie"]["in"] == "cookie"
    assert "csrfTokenHeader" in sec_schemes
    assert sec_schemes["csrfTokenHeader"]["name"] == "X-CSRF-Token"

    op_ids: list[str] = []
    api_op_count = 0
    for path, methods in sorted(disk_spec.get("paths", {}).items()):
        for method, op in sorted(methods.items()):
            if method not in {"get", "post", "put", "patch", "delete"}:
                continue
            oid = op.get("operationId")
            assert isinstance(oid, str) and oid.strip(), f"Missing operationId on {method} {path}"
            op_ids.append(oid)

            if path.startswith("/api/v1/"):
                api_op_count += 1
                responses = op.get("responses", {})
                assert any(code.startswith("2") for code in responses), (
                    f"Missing 2xx response schema on {method} {path}"
                )
                assert "default" in responses, (
                    f"Missing RFC 9457 default ProblemDetail response on {method} {path}"
                )
                default_content = responses["default"].get("content", {})
                assert "application/problem+json" in default_content

    duplicates = [oid for oid, cnt in Counter(op_ids).items() if cnt > 1]
    assert duplicates == [], f"Duplicate operationIds detected: {duplicates}"
    assert api_op_count >= 100, f"Expected >= 100 versioned /api/v1 operations, got {api_op_count}"


@pytest.mark.unit
def test_no_universal_arbitrary_agent_execution_endpoint_and_no_frontend_scaffolding() -> None:
    app = create_app(Settings(environment="local", debug=False))
    spec = app.openapi()

    prohibited_fragments = (
        "/agent/run",
        "/agent/execute",
        "/agents/invoke",
        "/llm/complete",
        "/llm/chat",
        "/tools/execute",
        "/eval",
        "/shell",
    )
    for path in spec.get("paths", {}):
        lowered = path.lower()
        for bad in prohibited_fragments:
            assert bad not in lowered, f"Prohibited arbitrary agent/tool endpoint exposed: {path}"

    # P15–P18 invariant: zero frontend changes or new frontend dependencies introduced
    if (REPO_ROOT / ".git").exists():
        import subprocess

        diff_proc = subprocess.run(
            ["git", "diff", "main", "--", "frontend"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
        assert diff_proc.stdout == "", "P15–P18 must not modify frontend/"


@pytest.mark.unit
def test_alembic_migration_chain_is_linear_and_enforces_postgres_rls_and_append_only_audit() -> (
    None
):
    cfg = Config(str(BACKEND_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_ROOT / "migrations"))
    script_dir = ScriptDirectory.from_config(cfg)

    heads = script_dir.get_heads()
    assert heads == ["0010_governance_audit_analytics_privacy"], (
        f"Expected single linear Alembic head 0010_governance_audit_analytics_privacy, got {heads}"
    )
    bases = script_dir.get_bases()
    assert len(bases) == 1

    walked = [rev.revision for rev in script_dir.walk_revisions()]
    assert len(walked) == 10
    assert walked[0] == "0010_governance_audit_analytics_privacy"

    versions_dir = BACKEND_ROOT / "migrations" / "versions"
    combined_sql_py = "\n".join(
        p.read_text(encoding="utf-8") for p in sorted(versions_dir.glob("*.py"))
    )

    for required_rls_table in (
        "audit_event",
        "job_operation",
        "candidate",
        "application",
        "document",
        "evidence_fragment",
        "candidate_fact_revision",
        "screening_run",
        "candidate_summary_revision",
        "competency_framework",
        "pack_revision",
        "feedback",
        "export_artifact",
        "organization_policy",
        "retention_hold",
        "privacy_request",
        "deletion_ledger",
    ):
        assert required_rls_table in combined_sql_py
        assert "ENABLE ROW LEVEL SECURITY" in combined_sql_py
        assert "FORCE ROW LEVEL SECURITY" in combined_sql_py

    assert "prevent_audit_event_mutation" in combined_sql_py
    assert "trg_audit_event_immutable" in combined_sql_py
    assert "trg_audit_event_no_update" in combined_sql_py
    assert "trg_audit_event_no_delete" in combined_sql_py


@pytest.mark.unit
def test_screening_determinism_never_fails_missing_preferred_skill_or_unreviewed_facts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    evidence_id = uuid4()
    monkeypatch.setattr(
        ScreeningService,
        "_fact_evidence_ids",
        staticmethod(lambda _uow, _tenant, _facts, _doc_ver: [evidence_id]),
    )
    tenant_id: UUID = uuid4()
    doc_ver_id: UUID = uuid4()
    mock_uow = cast(UnitOfWork, Mock())

    preferred_skill = Criterion(
        id=uuid4(),
        tenant_id=tenant_id,
        criterion_type=CriterionType.SKILL_PRESENT.value,
        configuration={"criterion_type": "SKILL_PRESENT", "skill_name": "Rust"},
        is_required=False,
        requires_human_review=False,
    )
    outcome_pref, expl_pref, _ = ScreeningService._evaluate(
        mock_uow, tenant_id, preferred_skill, [], doc_ver_id
    )
    assert outcome_pref == ScreeningOutcome.UNKNOWN
    assert expl_pref

    exp_criterion = Criterion(
        id=uuid4(),
        tenant_id=tenant_id,
        criterion_type=CriterionType.MIN_EXPERIENCE_MONTHS.value,
        configuration={"criterion_type": "MIN_EXPERIENCE_MONTHS", "min_months": 48},
        is_required=True,
        requires_human_review=False,
    )
    verified_exp = CandidateFactRevision(
        id=uuid4(),
        tenant_id=tenant_id,
        candidate_id=uuid4(),
        fact_key=uuid4(),
        revision_number=1,
        fact_type="EXPERIENCE_MONTHS",
        value="60 months",
        extracted_value="60 months",
        polarity=CandidateFactPolarity.AFFIRMED.value,
        extracted_polarity=CandidateFactPolarity.AFFIRMED.value,
        status=CandidateFactStatus.VERIFIED.value,
        extraction_run_id=uuid4(),
        created_by=uuid4(),
    )

    baseline = ScreeningService._evaluate(
        mock_uow, tenant_id, exp_criterion, [verified_exp], doc_ver_id
    )
    assert baseline[0] == ScreeningOutcome.PASS
    for _ in range(25):
        repeat = ScreeningService._evaluate(
            mock_uow, tenant_id, exp_criterion, [verified_exp], doc_ver_id
        )
        assert repeat[0] == baseline[0]
        assert repeat[1] == baseline[1]
        assert [f.id for f in repeat[2]] == [f.id for f in baseline[2]]
