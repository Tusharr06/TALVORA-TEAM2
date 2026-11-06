"""Port Interfaces for Object Storage and Malware Scanning.

Kept separate from `talvora.shared.application.ports` because these protocols are
specific to the document-intake boundary introduced in this phase; nothing outside
`documents` is expected to depend on them yet.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import BinaryIO, Protocol, runtime_checkable


@dataclass(frozen=True)
class PutObjectResult:
    """Confirmation that an object was durably written and checksum-validated."""

    bucket: str
    key: str
    byte_size: int
    etag: str | None


@runtime_checkable
class ObjectStoragePort(Protocol):
    """Private object storage abstraction. Never issues public/presigned URLs."""

    def put_object(
        self,
        bucket: str,
        key: str,
        body: bytes,
        content_type: str,
        sha256_checksum: str,
    ) -> PutObjectResult:
        """Durably write an object with a private ACL and verify it round-trips intact."""
        ...

    def get_object_stream(self, bucket: str, key: str) -> BinaryIO:
        """Open a readable, bounded stream for an existing object's bytes."""
        ...

    def copy_object(self, src_bucket: str, src_key: str, dest_bucket: str, dest_key: str) -> None:
        """Copy an object between buckets (used to promote quarantine -> clean)."""
        ...

    def delete_object(self, bucket: str, key: str) -> None:
        """Permanently delete an object. Used by orphan cleanup and retention deletion."""
        ...

    def object_exists(self, bucket: str, key: str) -> bool:
        """Check for object existence without downloading its body."""
        ...


class ScannerUnavailableError(Exception):
    """Raised when the malware scanner cannot be reached or times out.

    This is never treated as "clean" -- the caller must leave the item pending/retryable.
    """


@dataclass(frozen=True)
class ScanResult:
    """Actionable, typed malware-scan verdict."""

    infected: bool
    signature_name: str | None
    engine: str
    scanned_at_iso: str


@runtime_checkable
class VirusScannerPort(Protocol):
    """Malware scanning abstraction over a real ClamAV clamd daemon."""

    def scan_stream(self, data: bytes) -> ScanResult:
        """Scan a bounded in-memory buffer. Raises ScannerUnavailableError on failure."""
        ...

    def health_check(self) -> bool:
        """Return True only when the scanner responds to PING with PONG."""
        ...
