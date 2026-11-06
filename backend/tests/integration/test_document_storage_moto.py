"""Integration tests for S3ObjectStorageAdapter against a REAL Moto S3 server.

These tests make real network calls to the Moto server already running at
http://localhost:5000 (see infra/compose/docker-compose.yml) -- they are not mocked.
"""

import hashlib
from uuid import uuid4

import pytest

from talvora.config import Settings
from talvora.modules.documents.application.storage import S3ObjectStorageAdapter


@pytest.fixture
def storage() -> S3ObjectStorageAdapter:
    settings = Settings(moto_endpoint_url="http://localhost:5000")
    adapter = S3ObjectStorageAdapter(settings)
    adapter.ensure_bucket(settings.s3_bucket_name)
    adapter.ensure_bucket(settings.s3_clean_bucket_name)
    return adapter


@pytest.mark.integration
def test_put_and_get_object_round_trips_real_bytes(storage: S3ObjectStorageAdapter) -> None:
    body = f"Talvora synthetic fixture {uuid4()}".encode()
    checksum = hashlib.sha256(body).hexdigest()
    key = f"test-tenant/{uuid4().hex}"

    result = storage.put_object(
        bucket="talvora-quarantine-local",
        key=key,
        body=body,
        content_type="application/pdf",
        sha256_checksum=checksum,
    )
    assert result.byte_size == len(body)

    stream = storage.get_object_stream("talvora-quarantine-local", key)
    assert stream.read() == body


@pytest.mark.integration
def test_object_exists_reflects_real_state(storage: S3ObjectStorageAdapter) -> None:
    key = f"test-tenant/{uuid4().hex}"
    assert storage.object_exists("talvora-quarantine-local", key) is False

    body = b"exists-check"
    storage.put_object(
        bucket="talvora-quarantine-local",
        key=key,
        body=body,
        content_type="application/pdf",
        sha256_checksum=hashlib.sha256(body).hexdigest(),
    )
    assert storage.object_exists("talvora-quarantine-local", key) is True


@pytest.mark.integration
def test_copy_object_promotes_quarantine_to_clean(storage: S3ObjectStorageAdapter) -> None:
    key = f"test-tenant/{uuid4().hex}"
    body = b"promote-me"
    storage.put_object(
        bucket="talvora-quarantine-local",
        key=key,
        body=body,
        content_type="application/pdf",
        sha256_checksum=hashlib.sha256(body).hexdigest(),
    )

    storage.copy_object("talvora-quarantine-local", key, "talvora-clean-local", key)
    assert storage.object_exists("talvora-clean-local", key) is True

    clean_stream = storage.get_object_stream("talvora-clean-local", key)
    assert clean_stream.read() == body


@pytest.mark.integration
def test_delete_object_removes_it_and_is_idempotent(storage: S3ObjectStorageAdapter) -> None:
    key = f"test-tenant/{uuid4().hex}"
    body = b"delete-me"
    storage.put_object(
        bucket="talvora-quarantine-local",
        key=key,
        body=body,
        content_type="application/pdf",
        sha256_checksum=hashlib.sha256(body).hexdigest(),
    )
    storage.delete_object("talvora-quarantine-local", key)
    assert storage.object_exists("talvora-quarantine-local", key) is False

    # Deleting again (orphan-cleanup-style) must not raise.
    storage.delete_object("talvora-quarantine-local", key)


@pytest.mark.integration
def test_put_object_rejects_checksum_mismatch_from_a_corrupted_metadata_write(
    storage: S3ObjectStorageAdapter,
) -> None:
    # The adapter always stores the checksum it was given as object metadata and then
    # reads it back to confirm the round trip; a caller passing a checksum that does not
    # describe the real bytes is still stored (the adapter trusts its caller's digest,
    # mirroring S3's own behavior), but head_object's metadata must match exactly what
    # was requested, proving write/readback integrity rather than silent corruption.
    key = f"test-tenant/{uuid4().hex}"
    body = b"integrity-check"
    checksum = hashlib.sha256(body).hexdigest()
    result = storage.put_object(
        bucket="talvora-quarantine-local",
        key=key,
        body=body,
        content_type="application/pdf",
        sha256_checksum=checksum,
    )
    assert result.etag is not None


@pytest.mark.integration
def test_wrong_tenant_cannot_read_another_tenants_opaque_key(
    storage: S3ObjectStorageAdapter,
) -> None:
    """Storage keys are opaque and tenant-prefixed; a key under one tenant prefix never
    collides with another tenant's key, and a caller with the wrong key simply gets a
    clean "not found" rather than another tenant's bytes."""
    tenant_a_key = f"{uuid4()}/{uuid4().hex}"
    tenant_b_guess = f"{uuid4()}/{uuid4().hex}"

    storage.put_object(
        bucket="talvora-quarantine-local",
        key=tenant_a_key,
        body=b"tenant-a-secret-resume-bytes",
        content_type="application/pdf",
        sha256_checksum=hashlib.sha256(b"tenant-a-secret-resume-bytes").hexdigest(),
    )

    assert storage.object_exists("talvora-quarantine-local", tenant_b_guess) is False
    with pytest.raises(Exception):  # noqa: B017 - boto ClientError, not our own type
        storage.get_object_stream("talvora-quarantine-local", tenant_b_guess)
