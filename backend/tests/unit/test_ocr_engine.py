"""Unit tests for the bounded Tesseract OCR adapter's pure logic.

These tests never invoke the real `tesseract` binary (see
`tests/integration/test_real_ocr_extraction.py` for the mandated real,
native-package end-to-end OCR test) -- they exercise the TSV parsing, the
stripped-child-environment construction, and the timeout/failure error mapping.
"""

import glob
import os
import subprocess
import tempfile
from unittest.mock import patch

import pytest

from talvora.modules.evidence.application.ocr import OcrUnavailableError, TesseractOcrEngine

_SAMPLE_TSV = (
    "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n"
    "1\t1\t0\t0\t0\t0\t0\t0\t2778\t834\t-1\t\n"
    "2\t1\t1\t0\t0\t0\t141\t283\t371\t23\t-1\t\n"
    "5\t1\t1\t1\t1\t1\t141\t283\t68\t23\t93.29\tHello\n"
    "5\t1\t1\t1\t1\t2\t215\t283\t88\t23\t93.02\tTalvora\n"
    "5\t1\t2\t1\t1\t1\t311\t400\t56\t23\t10.50\tblurry\n"
)


def test_parse_tsv_reconstructs_line_grouped_text_and_mean_confidence() -> None:
    result = TesseractOcrEngine._parse_tsv(_SAMPLE_TSV)
    assert "Hello Talvora" in result.text
    assert "blurry" in result.text
    # Two lines (different block_num) joined by a newline.
    assert result.text.count("\n") >= 1
    assert result.mean_confidence is not None
    assert 10 < result.mean_confidence < 100


def test_parse_tsv_with_no_words_yields_none_confidence() -> None:
    header_only = "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n"
    result = TesseractOcrEngine._parse_tsv(header_only)
    assert result.text == ""
    assert result.mean_confidence is None


def test_child_env_is_stripped_to_path_and_optional_tessdata(tmp_path: object) -> None:
    binary_dir = str(tmp_path)
    engine = TesseractOcrEngine(tesseract_cmd=os.path.join(binary_dir, "tesseract.exe"))
    env = engine._child_env()
    assert set(env.keys()) <= {"PATH", "TESSDATA_PREFIX"}
    assert env["PATH"] == binary_dir


def test_child_env_includes_tessdata_prefix_when_directory_exists(tmp_path: object) -> None:
    binary_dir = str(tmp_path)
    os.makedirs(os.path.join(binary_dir, "tessdata"))
    engine = TesseractOcrEngine(tesseract_cmd=os.path.join(binary_dir, "tesseract.exe"))
    env = engine._child_env()
    assert env["TESSDATA_PREFIX"] == os.path.join(binary_dir, "tessdata")


def test_ocr_png_raises_unavailable_on_timeout() -> None:
    engine = TesseractOcrEngine(tesseract_cmd="tesseract", timeout_seconds=1.0)
    with patch(
        "talvora.modules.evidence.application.ocr.subprocess.run",
        side_effect=subprocess.TimeoutExpired(cmd="tesseract", timeout=1.0),
    ):
        with pytest.raises(OcrUnavailableError):
            engine.ocr_png(b"not a real png, irrelevant for this mocked path")


def test_ocr_png_raises_unavailable_when_binary_missing() -> None:
    engine = TesseractOcrEngine(tesseract_cmd="tesseract-definitely-does-not-exist")
    with pytest.raises(OcrUnavailableError):
        engine.ocr_png(b"\x89PNG\r\n\x1a\nirrelevant")


def test_ocr_png_raises_unavailable_on_nonzero_exit() -> None:
    engine = TesseractOcrEngine(tesseract_cmd="tesseract")
    fake_result = subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="boom")
    with patch("talvora.modules.evidence.application.ocr.subprocess.run", return_value=fake_result):
        with pytest.raises(OcrUnavailableError, match="boom"):
            engine.ocr_png(b"irrelevant")


def test_temp_directory_is_removed_even_after_timeout() -> None:
    """The TemporaryDirectory context manager must clean up on every exit path,
    including a timeout -- assert no stray directories remain under the system temp
    root with our prefix after a timeout is raised."""
    engine = TesseractOcrEngine(tesseract_cmd="tesseract", timeout_seconds=0.001)
    pattern = os.path.join(tempfile.gettempdir(), "talvora-ocr-*")
    before = set(glob.glob(pattern))
    with patch(
        "talvora.modules.evidence.application.ocr.subprocess.run",
        side_effect=subprocess.TimeoutExpired(cmd="tesseract", timeout=0.001),
    ):
        with pytest.raises(OcrUnavailableError):
            engine.ocr_png(b"irrelevant")
    after = set(glob.glob(pattern))
    assert after - before == set()
