"""Bounded Tesseract OCR Adapter.

Speaks to a real, locally installed Tesseract binary over a subprocess call --
mirroring the rigor of `talvora.modules.documents.application.scanner.ClamdScanner`
(bounded timeout, no hardcoded success, defensive cleanup) rather than depending on
a third-party wrapper's own, less controllable subprocess handling:

- A bounded, per-call timeout (`OcrUnavailableError` on timeout -- never a fabricated
  OCR result).
- A stripped child environment: only `PATH` (just enough to resolve the Tesseract
  binary's own shared libraries) and, if a `tessdata` directory sits next to the
  configured binary, `TESSDATA_PREFIX`. No inherited secrets, proxy variables, or
  network configuration are passed through.
- Every page image is written to its own `tempfile.TemporaryDirectory()`, which is
  guaranteed to be removed in its `__exit__` even when the subprocess call raises
  `TimeoutExpired` or any other exception -- so a timed-out or canceled OCR attempt
  never leaves a page image behind on disk.
- No network access is requested or required by this adapter; it never fetches a
  URL. Whether the OS-level sandbox additionally blocks *all* egress from the
  Tesseract child process is a runtime/container property this adapter cannot
  itself prove -- see the P07 completion report's "worker profile" section for what
  is actually enforced locally versus what needs staging verification.

Tesseract's `tsv` output mode is used (one row per page/block/par/line/word) so a
real, per-word confidence heuristic can be computed -- this is explicitly a
heuristic, never calibrated factual certainty about what the candidate wrote.
"""

from __future__ import annotations

import csv
import io
import os
import subprocess
import tempfile
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

_WORD_LEVEL = "5"


class OcrUnavailableError(Exception):
    """Raised when Tesseract cannot be invoked or times out.

    Mirrors `ScannerUnavailableError`: this is never silently treated as "empty page"
    by the caller -- it must be surfaced as an explicit coverage warning so a bounded
    partial extraction is never presented as a full, complete one.
    """


@dataclass(frozen=True)
class OcrPageResult:
    """One page's OCR text and a confidence heuristic (never a factual guarantee)."""

    text: str
    mean_confidence: float | None  # None when Tesseract detected zero words.


@runtime_checkable
class OcrEngine(Protocol):
    """The narrow interface `PdfDocumentExtractor` depends on -- lets tests supply a
    lightweight stub instead of a real `TesseractOcrEngine` without widening the
    production constructor's type to `object`."""

    def ocr_png(self, png_bytes: bytes) -> OcrPageResult:
        """OCR a single already-rendered page image. Raises `OcrUnavailableError` on
        failure/timeout -- never returns a fabricated result."""
        ...


class TesseractOcrEngine:
    """Bounded subprocess adapter over a real `tesseract` binary."""

    def __init__(
        self,
        tesseract_cmd: str = "tesseract",
        language: str = "eng",
        timeout_seconds: float = 20.0,
    ) -> None:
        self.tesseract_cmd = tesseract_cmd
        self.language = language
        self.timeout_seconds = timeout_seconds

    def _child_env(self) -> dict[str, str]:
        """A deliberately stripped environment: no inherited secrets or proxies."""
        binary_dir = os.path.dirname(self.tesseract_cmd) or "."
        env = {"PATH": binary_dir}
        tessdata_dir = os.path.join(binary_dir, "tessdata")
        if os.path.isdir(tessdata_dir):
            env["TESSDATA_PREFIX"] = tessdata_dir
        return env

    def ocr_png(self, png_bytes: bytes) -> OcrPageResult:
        """OCR a single already-rendered page image (PNG bytes).

        Writes the image to a bounded, auto-deleted temp directory; the directory
        (and the image/output files inside it) are removed on every exit path,
        including a timeout, via the `TemporaryDirectory` context manager's
        guaranteed `__exit__`.
        """
        with tempfile.TemporaryDirectory(prefix="talvora-ocr-") as tmp_dir:
            image_path = os.path.join(tmp_dir, "page.png")
            with open(image_path, "wb") as fh:
                fh.write(png_bytes)

            output_base = os.path.join(tmp_dir, "out")
            try:
                result = subprocess.run(  # noqa: S603 -- fixed argv, no shell, no user input in argv
                    [
                        self.tesseract_cmd,
                        image_path,
                        output_base,
                        "-l",
                        self.language,
                        "tsv",
                    ],
                    timeout=self.timeout_seconds,
                    env=self._child_env(),
                    cwd=tmp_dir,
                    capture_output=True,
                    text=True,
                    check=False,
                )
            except subprocess.TimeoutExpired as exc:
                raise OcrUnavailableError(
                    f"Tesseract timed out after {self.timeout_seconds}s."
                ) from exc
            except OSError as exc:
                raise OcrUnavailableError(f"Unable to invoke Tesseract: {exc}") from exc

            if result.returncode != 0:
                raise OcrUnavailableError(
                    f"Tesseract exited with code {result.returncode}: {result.stderr.strip()[:500]}"
                )

            tsv_path = output_base + ".tsv"
            try:
                with open(tsv_path, encoding="utf-8") as fh:
                    tsv_content = fh.read()
            except OSError as exc:
                raise OcrUnavailableError(
                    f"Tesseract produced no readable TSV output: {exc}"
                ) from exc

            return self._parse_tsv(tsv_content)

    @staticmethod
    def _parse_tsv(tsv_content: str) -> OcrPageResult:
        """Reconstruct line-grouped text and a mean word-confidence heuristic."""
        reader = csv.DictReader(io.StringIO(tsv_content), delimiter="\t")
        lines: dict[tuple[str, str, str], list[str]] = {}
        confidences: list[float] = []

        for row in reader:
            if row.get("level") != _WORD_LEVEL:
                continue
            word = (row.get("text") or "").strip()
            if not word:
                continue
            key = (row.get("block_num", ""), row.get("par_num", ""), row.get("line_num", ""))
            lines.setdefault(key, []).append(word)
            try:
                conf = float(row.get("conf", "-1"))
            except ValueError:
                conf = -1.0
            if conf >= 0:
                confidences.append(conf)

        text = "\n".join(" ".join(words) for words in lines.values())
        mean_confidence = sum(confidences) / len(confidences) if confidences else None
        return OcrPageResult(text=text, mean_confidence=mean_confidence)


__all__ = ["OcrEngine", "OcrPageResult", "OcrUnavailableError", "TesseractOcrEngine"]
