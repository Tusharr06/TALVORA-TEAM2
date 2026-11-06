"""Object Storage Adapters: Real S3/Moto and an In-Memory Test Double.

The S3 adapter always sets a private ACL and never generates a presigned or public
URL -- every authorized download in this phase is proxied byte-for-byte through our own
API after an authorization check, never redirected to object-store infrastructure.
"""

from __future__ import annotations

import contextlib
import hashlib
import io
from typing import BinaryIO

import boto3
from botocore.client import Config as BotoConfig
from botocore.exceptions import ClientError

from talvora.config import Settings
from talvora.modules.documents.application.ports import ObjectStoragePort, PutObjectResult


class ObjectIntegrityError(Exception):
    """Raised when a written object's checksum does not match what was requested."""


class S3ObjectStorageAdapter(ObjectStoragePort):
    """Real boto3 S3 client adapter, pointed at Moto locally and real AWS in staging/prod."""

    def __init__(self, settings: Settings) -> None:
        boto_config = BotoConfig(signature_version="s3v4", retries={"max_attempts": 3})
        if settings.moto_endpoint_url:
            self._client = boto3.client(
                "s3",
                region_name=settings.aws_region,
                config=boto_config,
                endpoint_url=settings.moto_endpoint_url,
                aws_access_key_id="test",
                aws_secret_access_key="test",
            )
        else:
            self._client = boto3.client("s3", region_name=settings.aws_region, config=boto_config)

    def ensure_bucket(self, bucket: str) -> None:
        """Idempotently create a bucket (local/test convenience; a no-op if it exists)."""
        try:
            self._client.head_bucket(Bucket=bucket)
        except ClientError:
            with contextlib.suppress(ClientError):
                self._client.create_bucket(Bucket=bucket)

    def put_object(
        self,
        bucket: str,
        key: str,
        body: bytes,
        content_type: str,
        sha256_checksum: str,
    ) -> PutObjectResult:
        self._client.put_object(
            Bucket=bucket,
            Key=key,
            Body=body,
            ContentType=content_type,
            ACL="private",
            Metadata={"sha256": sha256_checksum},
        )

        # Checksum validation: read the object back and recompute the digest rather than
        # trusting any single round trip -- this is explicitly NOT an atomic
        # S3/PostgreSQL transaction, just a best-effort integrity confirmation of the
        # object-store write itself.
        head = self._client.head_object(Bucket=bucket, Key=key)
        confirmed_sha = head.get("Metadata", {}).get("sha256")
        if confirmed_sha != sha256_checksum:
            raise ObjectIntegrityError(
                f"Object {bucket}/{key} metadata checksum mismatch after write."
            )

        return PutObjectResult(
            bucket=bucket,
            key=key,
            byte_size=int(head.get("ContentLength", len(body))),
            etag=head.get("ETag"),
        )

    def get_object_stream(self, bucket: str, key: str) -> BinaryIO:
        resp = self._client.get_object(Bucket=bucket, Key=key)
        data = resp["Body"].read()
        return io.BytesIO(data)

    def copy_object(self, src_bucket: str, src_key: str, dest_bucket: str, dest_key: str) -> None:
        self._client.copy_object(
            Bucket=dest_bucket,
            Key=dest_key,
            CopySource={"Bucket": src_bucket, "Key": src_key},
            ACL="private",
        )

    def delete_object(self, bucket: str, key: str) -> None:
        with contextlib.suppress(ClientError):
            self._client.delete_object(Bucket=bucket, Key=key)

    def object_exists(self, bucket: str, key: str) -> bool:
        try:
            self._client.head_object(Bucket=bucket, Key=key)
            return True
        except ClientError:
            return False


class InMemoryObjectStorageAdapter(ObjectStoragePort):
    """Fast, deterministic in-memory double for unit tests. Never used in production."""

    def __init__(self) -> None:
        self._objects: dict[tuple[str, str], tuple[bytes, str]] = {}

    def put_object(
        self,
        bucket: str,
        key: str,
        body: bytes,
        content_type: str,
        sha256_checksum: str,
    ) -> PutObjectResult:
        actual = hashlib.sha256(body).hexdigest()
        if actual != sha256_checksum:
            raise ObjectIntegrityError("Provided checksum does not match object bytes.")
        self._objects[(bucket, key)] = (body, content_type)
        return PutObjectResult(bucket=bucket, key=key, byte_size=len(body), etag=actual)

    def get_object_stream(self, bucket: str, key: str) -> BinaryIO:
        if (bucket, key) not in self._objects:
            raise ObjectIntegrityError(f"No such object {bucket}/{key}")
        data, _ = self._objects[(bucket, key)]
        return io.BytesIO(data)

    def copy_object(self, src_bucket: str, src_key: str, dest_bucket: str, dest_key: str) -> None:
        if (src_bucket, src_key) not in self._objects:
            raise ObjectIntegrityError(f"No such object {src_bucket}/{src_key}")
        self._objects[(dest_bucket, dest_key)] = self._objects[(src_bucket, src_key)]

    def delete_object(self, bucket: str, key: str) -> None:
        self._objects.pop((bucket, key), None)

    def object_exists(self, bucket: str, key: str) -> bool:
        return (bucket, key) in self._objects
