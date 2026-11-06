"""Version-Aware S3 Object Purge and Independent AES-256-GCM Encrypted S3 Tombstone Ledger (P17).

Implements Playbook Section 21.5:
- Purges ALL S3 object versions and delete markers (not merely the latest key), surfacing
  transient S3 errors explicitly rather than swallowing them.
- Persists an encrypted, PII-free tombstone payload in a dedicated S3 prefix/bucket and
  verifies its SHA-256 checksum before any database purge is marked complete.
- Enumerates and decrypts S3 tombstones during disaster-recovery restore replay so that
  candidates purged after an older database checkpoint are never resurrected.
"""

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from botocore.exceptions import BotoCoreError, ClientError

from talvora.modules.documents.application.ports import ObjectStoragePort
from talvora.shared.infrastructure.security import decrypt_token, encrypt_token


class PrivacyStoragePurgeError(RuntimeError):
    """Raised when one or more S3 object or version deletions fail during a privacy purge."""


@dataclass(frozen=True)
class TombstonePayload:
    """Opaque, strictly PII-free deletion tombstone stored encrypted in S3."""

    ledger_id: str
    tenant_id: str
    privacy_request_id: str | None
    candidate_id: str
    subject_hash: str
    purged_at_iso: str
    backup_window_expires_at_iso: str
    object_targets: list[dict[str, str]]  # [{"bucket": ..., "key": ...}]


@dataclass(frozen=True)
class TombstoneReceipt:
    bucket: str
    key: str
    sha256_checksum: str
    etag: str | None


def purge_all_object_versions(storage: ObjectStoragePort, bucket: str, key: str) -> int:
    """Deletes ALL historical S3 object versions and delete markers for `(bucket, key)`.

    Unlike `ObjectStoragePort.delete_object` (which suppresses `ClientError` for best-effort
    orphan cleanup), privacy purge MUST verify deletion and raise `PrivacyStoragePurgeError`
    on storage failures so partial failures remain visible and retryable.
    """
    deleted_versions = 0
    try:
        raw_client: Any | None = getattr(storage, "_client", None)
    except (ClientError, BotoCoreError, OSError) as exc:
        raise PrivacyStoragePurgeError(
            f"Failed to connect to S3 client while purging {bucket}/{key}: {exc}"
        ) from exc

    if raw_client is not None:
        try:
            paginator_kwargs: dict[str, Any] = {"Bucket": bucket, "Prefix": key}
            while True:
                resp = raw_client.list_object_versions(**paginator_kwargs)
                versions = [v for v in resp.get("Versions", []) if v.get("Key") == key]
                markers = [m for m in resp.get("DeleteMarkers", []) if m.get("Key") == key]
                for entry in versions + markers:
                    vid = entry.get("VersionId")
                    if vid and vid != "null":
                        raw_client.delete_object(Bucket=bucket, Key=key, VersionId=vid)
                    else:
                        raw_client.delete_object(Bucket=bucket, Key=key)
                    deleted_versions += 1

                if not resp.get("IsTruncated"):
                    break
                if resp.get("NextKeyMarker"):
                    paginator_kwargs["KeyMarker"] = resp["NextKeyMarker"]
                if resp.get("NextVersionIdMarker"):
                    paginator_kwargs["VersionIdMarker"] = resp["NextVersionIdMarker"]

            # Also issue unversioned delete in case bucket versioning was suspended/off
            raw_client.delete_object(Bucket=bucket, Key=key)
            if deleted_versions == 0:
                deleted_versions = 1
            return deleted_versions
        except (ClientError, BotoCoreError, OSError) as exc:
            raise PrivacyStoragePurgeError(
                f"Failed to purge S3 object versions for {bucket}/{key}: {exc}"
            ) from exc

    # Fallback for test doubles implementing ObjectStoragePort
    try:
        existed = storage.object_exists(bucket, key)
        storage.delete_object(bucket, key)
        if storage.object_exists(bucket, key):
            raise PrivacyStoragePurgeError(
                f"Object {bucket}/{key} still exists after delete_object call."
            )
        return 1 if existed else 0
    except PrivacyStoragePurgeError:
        raise
    except Exception as exc:
        raise PrivacyStoragePurgeError(
            f"Storage adapter failed deleting {bucket}/{key}: {exc}"
        ) from exc


def write_encrypted_s3_tombstone(
    storage: ObjectStoragePort,
    bucket: str,
    encryption_key: str,
    tombstone: TombstonePayload,
) -> TombstoneReceipt:
    """Encrypts `TombstonePayload` with AES-256-GCM and writes it durably to S3."""
    plaintext_json = json.dumps(asdict(tombstone), sort_keys=True, separators=(",", ":"))
    encrypted_str = encrypt_token(plaintext_json, encryption_key)
    body = encrypted_str.encode("utf-8")
    checksum = hashlib.sha256(body).hexdigest()
    key = f"tombstones/{tombstone.tenant_id}/{tombstone.ledger_id}.enc"

    try:
        res = storage.put_object(
            bucket=bucket,
            key=key,
            body=body,
            content_type="application/octet-stream",
            sha256_checksum=checksum,
        )
    except Exception as exc:
        raise PrivacyStoragePurgeError(
            f"Failed to write independent encrypted S3 tombstone {bucket}/{key}: {exc}"
        ) from exc

    return TombstoneReceipt(
        bucket=res.bucket,
        key=res.key,
        sha256_checksum=checksum,
        etag=res.etag,
    )


def list_and_decrypt_s3_tombstones(
    storage: ObjectStoragePort,
    bucket: str,
    encryption_key: str,
    tenant_id: UUID | None = None,
    purged_after: datetime | None = None,
) -> list[TombstonePayload]:
    """Lists and decrypts independent S3 tombstones for post-restore replay."""
    prefix = f"tombstones/{tenant_id}/" if tenant_id is not None else "tombstones/"
    keys: list[str] = []

    raw_client: Any | None = getattr(storage, "_client", None)
    if raw_client is not None:
        continuation: str | None = None
        while True:
            kwargs: dict[str, Any] = {"Bucket": bucket, "Prefix": prefix}
            if continuation:
                kwargs["ContinuationToken"] = continuation
            resp = raw_client.list_objects_v2(**kwargs)
            for obj in resp.get("Contents", []):
                k = obj.get("Key")
                if isinstance(k, str) and k.endswith(".enc"):
                    keys.append(k)
            if not resp.get("IsTruncated"):
                break
            continuation = resp.get("NextContinuationToken")
    else:
        mem_objects: dict[tuple[str, str], Any] | None = getattr(storage, "_objects", None)
        if mem_objects is not None:
            keys = sorted(
                k
                for (b, k) in mem_objects
                if b == bucket and k.startswith(prefix) and k.endswith(".enc")
            )

    records: list[TombstonePayload] = []
    for key in sorted(keys):
        stream = storage.get_object_stream(bucket, key)
        try:
            ciphertext_str = stream.read().decode("utf-8")
        finally:
            stream.close()
        decrypted_json = decrypt_token(ciphertext_str, encryption_key)
        parsed = json.loads(decrypted_json)
        rec = TombstonePayload(
            ledger_id=parsed["ledger_id"],
            tenant_id=parsed["tenant_id"],
            privacy_request_id=parsed.get("privacy_request_id"),
            candidate_id=parsed["candidate_id"],
            subject_hash=parsed["subject_hash"],
            purged_at_iso=parsed["purged_at_iso"],
            backup_window_expires_at_iso=parsed["backup_window_expires_at_iso"],
            object_targets=list(parsed.get("object_targets", [])),
        )
        if purged_after is not None:
            rec_ts = datetime.fromisoformat(rec.purged_at_iso)
            if rec_ts < purged_after:
                continue
        records.append(rec)

    records.sort(key=lambda r: r.purged_at_iso)
    return records
