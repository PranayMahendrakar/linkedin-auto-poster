"""Fetch a paper's PDF (Zenodo first, Life of Research as a fallback) and read its text."""

import re

import requests

from research import config
from research.catalog import get_json


def _fetch(url, dest):
    headers = {"User-Agent": config.USER_AGENT}
    with requests.get(url, headers=headers, timeout=180, stream=True) as r:
        if r.status_code != 200:
            raise RuntimeError(f"HTTP {r.status_code}")
        size = 0
        with open(dest, "wb") as f:
            for chunk in r.iter_content(1 << 16):
                size += len(chunk)
                if size > config.DOC_MAX_BYTES:
                    raise RuntimeError("larger than LinkedIn's 100 MB limit")
                f.write(chunk)
    with open(dest, "rb") as f:
        if f.read(5) != b"%PDF-":
            raise RuntimeError("not a PDF")
    return dest


def lor_pdf_url(paper):
    if not paper.get("lor_id"):
        return None
    record = get_json(f"{config.LOR_BASE}/api/records/{paper['lor_id']}")
    for f in record.get("files") or []:
        if (f.get("name") or "").lower().endswith(".pdf"):
            return f.get("download_url")
    return None


def download(paper, dest):
    problems = []
    for source, url in (("Zenodo", lambda: paper.get("pdf_url")), ("Life of Research", lambda: lor_pdf_url(paper))):
        try:
            link = url()
            if link:
                return _fetch(link, dest)
            problems.append(f"{source}: no PDF link")
        except Exception as e:                       # noqa: BLE001 - try the next source
            problems.append(f"{source}: {e}")
    raise RuntimeError(f"Could not download the PDF for {paper['short_title']!r} ({'; '.join(problems)})")


def within_page_limit(paper_pdf, out_pdf):
    """The paper as-is, or (rarely) a copy cut to LinkedIn's 300-page limit."""
    from pypdf import PdfReader, PdfWriter

    try:
        reader = PdfReader(paper_pdf)
        if reader.is_encrypted:
            reader.decrypt("")
        pages = len(reader.pages)
    except Exception as e:                           # noqa: BLE001 - let LinkedIn judge it
        print(f"::warning::Could not count pages in {paper_pdf} ({e}); uploading it as is")
        return paper_pdf, None
    if pages <= config.DOC_MAX_PAGES:
        return paper_pdf, pages
    print(f"::warning::The paper has {pages} pages; uploading the first {config.DOC_MAX_PAGES} (LinkedIn's limit)")
    writer = PdfWriter()
    for page in reader.pages[:config.DOC_MAX_PAGES]:
        writer.add_page(page)
    with open(out_pdf, "wb") as f:
        writer.write(f)
    return out_pdf, config.DOC_MAX_PAGES


def extract_text(path, limit=None):
    """The paper's text, for the writer to read and the number check to search."""
    from pypdf import PdfReader

    try:
        reader = PdfReader(path)
        if reader.is_encrypted:
            reader.decrypt("")
        text = "\n".join((page.extract_text() or "") for page in reader.pages)
    except Exception as e:                           # noqa: BLE001 - a scanned or odd PDF still posts
        print(f"::warning::Could not read text from {path}: {e}")
        return ""
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text[:limit] if limit else text
