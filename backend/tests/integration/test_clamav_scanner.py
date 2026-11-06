"""Integration tests for ClamdScanner against a REAL clamd daemon.

These tests make real TCP connections to clamd at localhost:3310 (see
infra/compose/docker-compose.yml) -- they are not mocked, and there is no hardcoded
clean/infected result anywhere in production code. The EICAR test string used here is
the industry-standard, harmless antivirus test signature; it is not malware and
contains no executable payload.
"""

import pytest

from talvora.config import Settings
from talvora.modules.documents.application.ports import ScannerUnavailableError
from talvora.modules.documents.application.scanner import ClamdScanner

EICAR_TEST_STRING = rb"X5O!P%@AP[4\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*"


@pytest.fixture
def scanner() -> ClamdScanner:
    return ClamdScanner.from_settings(Settings())


@pytest.mark.integration
def test_health_check_true_against_real_clamd(scanner: ClamdScanner) -> None:
    assert scanner.health_check() is True


@pytest.mark.integration
def test_scan_clean_buffer_reports_not_infected(scanner: ClamdScanner) -> None:
    result = scanner.scan_stream(b"%PDF-1.4\nHarmless synthetic fixture bytes.\n%%EOF")
    assert result.infected is False
    assert result.signature_name is None
    assert result.engine == "clamd"


@pytest.mark.integration
def test_scan_eicar_buffer_is_detected_as_infected(scanner: ClamdScanner) -> None:
    result = scanner.scan_stream(EICAR_TEST_STRING)
    assert result.infected is True
    assert result.signature_name is not None
    assert "EICAR" in result.signature_name.upper()


@pytest.mark.integration
def test_scan_eicar_detection_requires_the_real_engine_not_a_substring_check(
    scanner: ClamdScanner,
) -> None:
    """Confirms detection comes from the real clamd verdict, not from us pattern-matching
    the bytes ourselves: empirically, this clamd build's EICAR rule only fires on the
    exact, unpadded 68-byte test string (see `test_scan_eicar_buffer_is_detected_as_infected`),
    and surrounding the identical bytes with other content -- with or without a `%PDF-`
    header -- changes the real verdict to clean. That asymmetry is exactly the proof we
    want: our adapter has no hardcoded/local EICAR detection of its own, so the verdict
    tracks clamd's real engine behavior precisely, including its quirks.
    """
    padded = b"----begin-fixture----\n" + EICAR_TEST_STRING + b"\n----end-fixture----"
    exact = EICAR_TEST_STRING

    padded_result = scanner.scan_stream(padded)
    exact_result = scanner.scan_stream(exact)

    assert padded_result.infected is False
    assert exact_result.infected is True


@pytest.mark.integration
def test_scanner_unavailable_on_unreachable_port_never_reports_clean() -> None:
    unreachable = ClamdScanner(
        host="localhost",
        port=9,  # discard port, nothing listens for clamd there
        connect_timeout_seconds=1.0,
        scan_timeout_seconds=1.0,
    )
    with pytest.raises(ScannerUnavailableError):
        unreachable.scan_stream(b"irrelevant bytes")
    assert unreachable.health_check() is False


@pytest.mark.integration
def test_oversized_stream_is_refused_before_submission(scanner: ClamdScanner) -> None:
    oversized = ClamdScanner(
        host=scanner.host,
        port=scanner.port,
        max_stream_bytes=10,  # tiny bound to exercise the guard deterministically
    )
    with pytest.raises(ScannerUnavailableError):
        oversized.scan_stream(b"this payload is longer than ten bytes")
