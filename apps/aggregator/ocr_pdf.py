"""Tesseract OCR helper for scanned court PDFs — the default
``INDIACOURTS_OCR_COMMAND`` backend on the Spark tenant.

Invoked by ``command_ocr_backend`` as ``python -m apps.aggregator.ocr_pdf
<pdf-path>``; extracted text goes to stdout. Shipped as a module (not under
``scripts/``) because the OCR command runs INSIDE the refresh container and
the image carries only ``apps/`` + ``shared/``. The binaries it shells out to
(``pdftoppm`` from poppler-utils, ``tesseract``) exist only in the Docker
``spark`` stage — the Cloud Run runtime stage stays lean and never runs OCR.

Quality bar: the lexicon scan needs keyword-grade text, not a perfect
transcription — classic Tesseract on printed court orders clears that. A
VLM OCR (olmOCR on sparky) remains the benched upgrade path via the same
command hook if recall ever disappoints.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path

# Court orders are short; OCR-ing a giant annexure-laden PDF would blow the
# hook's 300s budget. The downstream head+tail truncation keeps long
# documents useful, so a page cap loses little.
MAX_PAGES = 30
DPI = 200


def ocr_pdf(
    pdf_path: str, *, langs: str = "eng", max_pages: int = MAX_PAGES, dpi: int = DPI
) -> str:
    """Rasterize with pdftoppm, OCR each page with tesseract, join the text."""
    with tempfile.TemporaryDirectory(prefix="indiacourts-ocr-") as tmp:
        prefix = str(Path(tmp) / "page")
        subprocess.run(
            [
                "pdftoppm",
                "-gray",
                "-r",
                str(dpi),
                "-f",
                "1",
                "-l",
                str(max_pages),
                "-png",
                pdf_path,
                prefix,
            ],
            capture_output=True,
            timeout=120,
            check=True,
        )
        pages = sorted(Path(tmp).glob("page-*.png")) or sorted(Path(tmp).glob("page*.png"))
        chunks: list[str] = []
        for page in pages:
            proc = subprocess.run(
                ["tesseract", str(page), "stdout", "-l", langs],
                capture_output=True,
                text=True,
                timeout=60,
                check=True,
            )
            chunks.append(proc.stdout)
        return "\n".join(chunks)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("pdf", help="Path to the PDF to OCR")
    ap.add_argument("--langs", default="eng", help="Tesseract language(s), e.g. eng or eng+hin")
    ap.add_argument("--max-pages", type=int, default=MAX_PAGES)
    args = ap.parse_args(argv)
    try:
        sys.stdout.write(ocr_pdf(args.pdf, langs=args.langs, max_pages=args.max_pages))
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or b"")
        if isinstance(detail, bytes):
            detail = detail.decode(errors="replace")
        print(f"ocr_pdf: {exc.cmd[0]} failed: {detail.strip()[:200]}", file=sys.stderr)
        return 1
    except subprocess.TimeoutExpired as exc:
        print(f"ocr_pdf: {exc.cmd[0]} timed out", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
