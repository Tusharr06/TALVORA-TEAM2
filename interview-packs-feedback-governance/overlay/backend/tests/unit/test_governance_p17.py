"""Unit tests for P17 Governance: Audit Immutability/Minimization, S3 Versioning & Tombstones."""

from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import boto3
import pytest
import sqlalchemy as sa
from botocore.client import Config as BotoConfig
from cryptography.exceptions import InvalidTag
from moto.server import ThreadedMotoServer
from sqlalchemy.exc import IntegrityError

from talvora.config import Settings
from talvora.modules.audit.domain.models import (
    AuditActorType,
    AuditEvent,
    AuditImmutableError,
    derive_audit_actor_type,
    sanitize_audit_details,
)
from talvora.modules.documents.application.storage import S3ObjectStorageAdapter
from talvora.modules.organizations.domain.models import Organization
from talvora.modules.privacy.application.tombstone_storage import (
    TombstonePayload,
    list_and_decrypt_s3_tombstones,
    purge_all_object_versions,
    write_encrypted_s3_tombstone,
)
from talvora.shared.infrastructure.db import DatabaseManager
from talvora.shared.infrastructure.models import Base


@pytest.fixture(scope="module")
def moto_endpoint() -> Generator[str]:
    server = ThreadedMotoServer(ip_address="127.0.0.1", port=0)
    server.start()
    host, port = server.get_host_and_port()
    try:
        yield f"http://{host}:{port}"
    finally:
        server.stop()


def test_audit_minimization_redacts_secrets_resumes_and_truncates_long_text() -> None:
    raw = {
        "access_token": "ya29.super-secret-google-token",
        "nested": {
            "refresh_token": "1//0g-secret",
            "resume_text": "Full candidate CV text that must never enter audit",
            "email_body": "Dear Candidate, private message...",
            "safe_summary": "Updated stage from NEW to IN_REVIEW",
        },
        "oversized": "x" * 900,
    }
    cleaned = sanitize_audit_details(raw)
    assert cleaned["access_token"] == "[REDACTED_SENSITIVE_FIELD]"
    assert cleaned["nested"]["refresh_token"] == "[REDACTED_SENSITIVE_FIELD]"
    assert cleaned["nested"]["resume_text"] == "[REDACTED_SENSITIVE_FIELD]"
    assert cleaned["nested"]["email_body"] == "[REDACTED_SENSITIVE_FIELD]"
    assert cleaned["nested"]["safe_summary"] == "Updated stage from NEW to IN_REVIEW"
    assert len(cleaned["oversized"]) < 520
    assert cleaned["oversized"].endswith("...[TRUNCATED]")

    assert derive_audit_actor_type(None, None, {}) == AuditActorType.SYSTEM_WORKER.value
    assert (
        derive_audit_actor_type(None, "PUBLIC_CANDIDATE_LINK", {})
        == AuditActorType.CANDIDATE_PUBLIC.value
    )
    assert (
        derive_audit_actor_type(uuid4(), "IDENTITY_BOOTSTRAP", {})
        == AuditActorType.IDENTITY_BOOTSTRAP.value
    )
    assert (
        derive_audit_actor_type(uuid4(), "TRANSITION_STAGE", {})
        == AuditActorType.HUMAN_MEMBER.value
    )


def test_audit_event_denies_orm_and_raw_sql_update_and_delete(test_settings: Settings) -> None:
    db_mgr = DatabaseManager(test_settings)
    engine = db_mgr.sync_engine
    Base.metadata.create_all(engine)
    session_local = db_mgr.session_maker

    with session_local() as session:
        org = Organization(id=uuid4(), name="Audit Guard Org", slug="audit-guard")
        session.add(org)
        session.flush()

        ev = AuditEvent(
            id=uuid4(),
            tenant_id=org.id,
            actor_id=uuid4(),
            actor_capability="CREATE_JOB",
            resource_type="job",
            resource_id=uuid4(),
            action="JOB_CREATED",
            details={"access_token": "should-redact", "title": "Platform Eng"},
        )
        session.add(ev)
        session.commit()

        # Verify before_insert minimization fired automatically
        assert ev.details["access_token"] == "[REDACTED_SENSITIVE_FIELD]"

        # 1. ORM update denied
        ev.action = "TAMPERED"
        with pytest.raises(AuditImmutableError):
            session.flush()
        session.rollback()

        # 2. ORM delete denied
        ev_reloaded = session.query(AuditEvent).filter_by(id=ev.id).one()
        session.delete(ev_reloaded)
        with pytest.raises(AuditImmutableError):
            session.flush()
        session.rollback()

        # 3. Direct SQL UPDATE denied by DB trigger
        with pytest.raises(IntegrityError, match="append-only"):
            session.execute(
                sa.text("UPDATE audit_event SET action = 'SQL_TAMPER' WHERE id = :eid"),
                {"eid": ev.id.hex},
            )
        session.rollback()

        # 4. Direct SQL DELETE denied by DB trigger while tenant exists
        with pytest.raises(IntegrityError, match="append-only"):
            session.execute(sa.text("DELETE FROM audit_event"))
        session.rollback()


def test_versioned_s3_purge_removes_all_historical_object_versions_and_encrypts_tombstones(
    moto_endpoint: str,
) -> None:
    settings = Settings(
        environment="test",
        moto_endpoint_url=moto_endpoint,
        s3_clean_bucket_name=f"versioned-clean-{uuid4().hex[:8]}",
    )
    storage = S3ObjectStorageAdapter(settings)
    bucket = settings.s3_clean_bucket_name
    storage.ensure_bucket(bucket)

    s3_raw = boto3.client(
        "s3",
        region_name=settings.aws_region,
        endpoint_url=moto_endpoint,
        aws_access_key_id="test",
        aws_secret_access_key="test",
        config=BotoConfig(signature_version="s3v4"),
    )
    # Enable S3 bucket versioning!
    s3_raw.put_bucket_versioning(
        Bucket=bucket,
        VersioningConfiguration={"Status": "Enabled"},
    )

    key = f"{uuid4()}/resume-versioned.pdf"
    for idx in range(3):
        body = f"resume-revision-{idx}".encode()
        s3_raw.put_object(Bucket=bucket, Key=key, Body=body)

    before_versions = s3_raw.list_object_versions(Bucket=bucket, Prefix=key).get("Versions", [])
    assert len(before_versions) == 3

    # Purge ALL historical object versions (not just latest key!)
    deleted_cnt = purge_all_object_versions(storage, bucket, key)
    assert deleted_cnt >= 3

    after_resp = s3_raw.list_object_versions(Bucket=bucket, Prefix=key)
    remaining_versions = [v for v in after_resp.get("Versions", []) if v.get("Key") == key]
    remaining_markers = [m for m in after_resp.get("DeleteMarkers", []) if m.get("Key") == key]
    assert remaining_versions == []
    assert remaining_markers == []

    # Test independent AES-256-GCM encrypted S3 tombstone round-trip & zero plaintext leakage
    tenant_id = uuid4()
    cand_id = uuid4()
    now = datetime(2026, 11, 6, 12, 0, tzinfo=UTC)
    enc_key = "unit-test-32-byte-aes-gcm-secret-key!!"
    ts = TombstonePayload(
        ledger_id=str(uuid4()),
        tenant_id=str(tenant_id),
        privacy_request_id=str(uuid4()),
        candidate_id=str(cand_id),
        subject_hash="a" * 64,
        purged_at_iso=now.isoformat(),
        backup_window_expires_at_iso=(now + timedelta(days=35)).isoformat(),
        object_targets=[{"bucket": bucket, "key": key}],
    )
    receipt = write_encrypted_s3_tombstone(storage, bucket, enc_key, ts)
    raw_stored = storage.get_object_stream(bucket, receipt.key).read()
    # Must NOT leak candidate_id or tenant_id in plaintext inside object body!
    assert str(cand_id).encode() not in raw_stored

    # Wrong decryption key fails AES-GCM authentication tag check
    with pytest.raises(InvalidTag):
        list_and_decrypt_s3_tombstones(storage, bucket, "wrong-secret-key-0000000000000000")

    recovered = list_and_decrypt_s3_tombstones(storage, bucket, enc_key, tenant_id=tenant_id)
    assert len(recovered) == 1
    assert recovered[0].candidate_id == str(cand_id)
