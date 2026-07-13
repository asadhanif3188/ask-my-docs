"""Fetch a small real-document corpus for end-to-end pipeline testing:
recent 10-K filings for AAPL, MSFT, NVDA from SEC EDGAR's public API.

SEC EDGAR serves 10-Ks as HTML, but app/ingestion/pipeline.parse_pdf() only
reads PDFs. Rather than extend the ingestion pipeline for this one-off proof
run, each filing's real text is extracted (BeautifulSoup) and rendered into a
plain PDF (reportlab) — content is genuine SEC filing text, just without the
original visual layout/tables. Also writes one deliberately truncated PDF to
exercise the quarantine path.

Requires the "corpus" extra: uv sync --extra corpus
Usage: uv run python -m scripts.fetch_corpus

Polite by SEC's own request: identifies itself with a descriptive User-Agent
and sleeps between requests (SEC's stated ceiling is 10 req/s; this script
stays far under it). See https://www.sec.gov/os/webmaster-faq#developers
"""

import time
import urllib.request
from pathlib import Path

from bs4 import BeautifulSoup
from reportlab.lib.pagesizes import LETTER
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer
from reportlab.lib.styles import getSampleStyleSheet
from xml.sax.saxutils import escape

USER_AGENT = "AskMyDocs-Research asadhanif3188@gmail.com"
REQUEST_DELAY_S = 0.5
FILINGS_PER_TICKER = 4
MAX_EXTRACTED_CHARS = 200_000  # bounds conversion time / PDF size; still real content

CORPUS_DIR = Path(__file__).resolve().parent.parent / "corpus"

TICKERS = {
    "AAPL": "0000320193",
    "MSFT": "0000789019",
    "NVDA": "0001045810",
}


def _fetch(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read()


def _recent_10k_filings(cik: str, limit: int) -> list[dict]:
    data = _fetch(f"https://data.sec.gov/submissions/CIK{cik}.json")
    import json

    submissions = json.loads(data)
    recent = submissions["filings"]["recent"]
    filings = [
        {
            "form": form,
            "accessionNumber": recent["accessionNumber"][i],
            "primaryDocument": recent["primaryDocument"][i],
            "filingDate": recent["filingDate"][i],
        }
        for i, form in enumerate(recent["form"])
        if form == "10-K"
    ]
    return filings[:limit]


def _filing_url(cik: str, filing: dict) -> str:
    accession_no_dashes = filing["accessionNumber"].replace("-", "")
    cik_no_leading_zeros = str(int(cik))
    return (
        f"https://www.sec.gov/Archives/edgar/data/{cik_no_leading_zeros}/"
        f"{accession_no_dashes}/{filing['primaryDocument']}"
    )


def _extract_text(html: bytes) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style"]):
        tag.decompose()
    text = soup.get_text(separator="\n")
    lines = [line.strip() for line in text.splitlines()]
    text = "\n".join(line for line in lines if line)
    return text[:MAX_EXTRACTED_CHARS]


def _render_pdf(text: str, out_path: Path, title: str) -> None:
    styles = getSampleStyleSheet()
    doc = SimpleDocTemplate(str(out_path), pagesize=LETTER)
    story = [Paragraph(escape(title), styles["Title"]), Spacer(1, 12)]
    for paragraph in text.split("\n"):
        if paragraph:
            story.append(Paragraph(escape(paragraph), styles["Normal"]))
            story.append(Spacer(1, 4))
    doc.build(story)


def _make_corrupted_copy(source_pdf: Path, out_path: Path) -> None:
    """Truncates a valid PDF partway through its byte stream, breaking its
    xref/trailer structure so pypdf fails to parse it — proves the
    quarantine-don't-crash path (tests/test_ingestion_lifecycle.py covers the
    same contract in isolation; this proves it against a real ingest run)."""
    data = source_pdf.read_bytes()
    out_path.write_bytes(data[: len(data) // 3])


def main() -> None:
    CORPUS_DIR.mkdir(exist_ok=True)
    written: list[Path] = []

    for ticker, cik in TICKERS.items():
        filings = _recent_10k_filings(cik, FILINGS_PER_TICKER)
        for filing in filings:
            url = _filing_url(cik, filing)
            print(f"{ticker} {filing['filingDate']}: fetching {url}")
            time.sleep(REQUEST_DELAY_S)
            try:
                html = _fetch(url)
            except Exception as exc:
                print(f"  skip (fetch failed: {exc})")
                continue

            text = _extract_text(html)
            out_path = CORPUS_DIR / f"{ticker}_10K_{filing['filingDate']}.pdf"
            _render_pdf(text, out_path, title=f"{ticker} 10-K filed {filing['filingDate']}")
            written.append(out_path)
            print(f"  wrote {out_path.name} ({out_path.stat().st_size:,} bytes)")

    if written:
        corrupted_path = CORPUS_DIR / "CORRUPTED_truncated_sample.pdf"
        _make_corrupted_copy(written[-1], corrupted_path)
        print(f"wrote deliberately truncated {corrupted_path.name} (quarantine-path fixture)")

    print(f"\n{len(written)} real documents + 1 corrupted fixture in {CORPUS_DIR}")


if __name__ == "__main__":
    main()
