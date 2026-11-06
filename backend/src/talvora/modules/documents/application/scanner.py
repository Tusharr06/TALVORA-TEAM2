"""Real ClamAV (clamd) Scan Adapter.

Speaks the clamd `INSTREAM` wire protocol directly over a bounded TCP socket: the
payload is sent in explicit, size-prefixed chunks with an overall byte cap and a
connect/recv timeout, and the daemon's one-line verdict is parsed into a typed,
actionable `ScanResult`. There is no hardcoded clean result anywhere in this module --
every `ScanResult(infected=False, ...)` comes from clamd actually saying `stream: OK`,
and every unreachable/timed-out/malformed-response case raises `ScannerUnavailableError`
so the caller leaves the item pending/retryable rather than marking it clean.
"""

from __future__ import annotations

import socket
from datetime import UTC, datetime

from talvora.config import Settings
from talvora.modules.documents.application.ports import (
    ScannerUnavailableError,
    ScanResult,
    VirusScannerPort,
)

_CHUNK_HEADER_SIZE = 4  # clamd INSTREAM length-prefix size, big-endian uint32


class ClamdScanner(VirusScannerPort):
    """TCP adapter for a real clamd daemon's INSTREAM protocol."""

    def __init__(
        self,
        host: str,
        port: int,
        connect_timeout_seconds: float = 5.0,
        scan_timeout_seconds: float = 30.0,
        max_stream_bytes: int = 15 * 1024 * 1024,
        chunk_bytes: int = 256 * 1024,
    ) -> None:
        self.host = host
        self.port = port
        self.connect_timeout_seconds = connect_timeout_seconds
        self.scan_timeout_seconds = scan_timeout_seconds
        self.max_stream_bytes = max_stream_bytes
        self.chunk_bytes = chunk_bytes

    @classmethod
    def from_settings(cls, settings: Settings) -> ClamdScanner:
        return cls(
            host=settings.clamav_host,
            port=settings.clamav_port,
            connect_timeout_seconds=settings.clamav_connect_timeout_seconds,
            scan_timeout_seconds=settings.clamav_scan_timeout_seconds,
            max_stream_bytes=settings.clamav_max_stream_bytes,
            chunk_bytes=settings.clamav_chunk_bytes,
        )

    def _connect(self) -> socket.socket:
        try:
            sock = socket.create_connection(
                (self.host, self.port), timeout=self.connect_timeout_seconds
            )
        except OSError as exc:
            raise ScannerUnavailableError(
                f"Unable to connect to clamd at {self.host}:{self.port}: {exc}"
            ) from exc
        sock.settimeout(self.scan_timeout_seconds)
        return sock

    def health_check(self) -> bool:
        """Return True only when clamd responds PING -> PONG within the connect timeout."""
        try:
            sock = self._connect()
        except ScannerUnavailableError:
            return False
        try:
            sock.sendall(b"zPING\0")
            data = sock.recv(64)
            return data.strip(b"\0") == b"PONG"
        except OSError:
            return False
        finally:
            sock.close()

    def scan_stream(self, data: bytes) -> ScanResult:
        if len(data) > self.max_stream_bytes:
            raise ScannerUnavailableError(
                f"Payload of {len(data)} bytes exceeds the scanner's bounded stream "
                f"limit of {self.max_stream_bytes} bytes; refusing to submit."
            )

        sock = self._connect()
        try:
            sock.sendall(b"zINSTREAM\0")

            offset = 0
            while offset < len(data):
                chunk = data[offset : offset + self.chunk_bytes]
                offset += len(chunk)
                sock.sendall(len(chunk).to_bytes(_CHUNK_HEADER_SIZE, "big"))
                sock.sendall(chunk)

            # Zero-length chunk terminates the stream.
            sock.sendall((0).to_bytes(_CHUNK_HEADER_SIZE, "big"))

            response = self._recv_response(sock)
        except TimeoutError as exc:
            raise ScannerUnavailableError(
                f"Timed out communicating with clamd at {self.host}:{self.port}: {exc}"
            ) from exc
        except OSError as exc:
            raise ScannerUnavailableError(
                f"I/O error communicating with clamd at {self.host}:{self.port}: {exc}"
            ) from exc
        finally:
            sock.close()

        return self._parse_response(response)

    def _recv_response(self, sock: socket.socket) -> str:
        chunks: list[bytes] = []
        while True:
            part = sock.recv(4096)
            if not part:
                break
            chunks.append(part)
            if part.endswith(b"\0"):
                break
        raw = b"".join(chunks)
        if not raw:
            raise ScannerUnavailableError("clamd closed the connection without a response.")
        return raw.decode("utf-8", errors="replace").strip("\0").strip()

    def _parse_response(self, response: str) -> ScanResult:
        scanned_at = datetime.now(UTC).isoformat()

        # Typical lines: "stream: OK", "stream: Eicar-Test-Signature FOUND",
        # "stream: <reason> ERROR".
        if response.endswith("OK"):
            return ScanResult(
                infected=False, signature_name=None, engine="clamd", scanned_at_iso=scanned_at
            )

        if response.endswith("FOUND"):
            # Format: "stream: <SignatureName> FOUND"
            body = response.split(":", 1)[-1].strip()
            signature = body[: -len(" FOUND")].strip() if body.endswith(" FOUND") else body
            return ScanResult(
                infected=True,
                signature_name=signature or "UNKNOWN",
                engine="clamd",
                scanned_at_iso=scanned_at,
            )

        if response.endswith("ERROR"):
            raise ScannerUnavailableError(f"clamd reported a scan error: {response}")

        raise ScannerUnavailableError(f"clamd returned an unrecognized response: {response!r}")
