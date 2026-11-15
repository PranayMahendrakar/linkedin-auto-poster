"""
Build the queue of papers to post: Pranay's Zenodo records, oldest first, each
matched to its Life of Research page.

    python -m research.catalog          # refresh research/catalog.json and summarise

Sources:
  * Zenodo public search, filtered by ORCID. Zenodo is the source of truth for
    which papers exist and for the PDF.
  * Life of Research public API (/api/records). Used only to find each paper's
    page there, matched by DOI first and title second. It also hosts other
    authors' papers and lags Zenodo by a day or so, so it never decides what
    gets posted. A paper it doesn't have yet links to its DOI instead.

If either source is down, the last saved catalog.json is used, so a Zenodo
outage never stops the daily post.
"""

import html
import json
import re
import sys
import time
from datetime import datetime

import requests

from research import config

DUPLICATE_MARK = "DUPLICATE"


# ============ HTTP ============
def get_json(url, params=None, attempts=3):
    headers = {"User-Agent": config.USER_AGENT, "Accept": "application/json"}
    last = None
    for attempt in range(1, attempts + 1):
        try:
            r = requests.get(url, params=params, headers=headers, timeout=config.HTTP_TIMEOUT)
            if r.status_code == 200:
                return r.json()
            last = f"HTTP {r.status_code}: {r.text[:200]}"
            if r.status_code < 500 and r.status_code != 429:
                break
        except (requests.RequestException, ValueError) as e:
            last = str(e)
        time.sleep(3 * attempt)
    raise RuntimeError(f"GET {url} failed: {last}")


def fetch_zenodo():
    """Every public Zenodo record with this ORCID among its creators (latest versions)."""
    size, page, records = 25, 1, []      # anonymous search allows at most 25 a page
    while True:
        data = get_json(config.ZENODO_API, {
            "q": f'creators.orcid:"{config.ORCID}"',
            "size": size, "page": page, "sort": "oldest",
        })
        batch = data.get("hits", {}).get("hits", [])
        records.extend(batch)
        if len(batch) < size or page >= 80:
            return records
        page += 1


def fetch_lor():
    """Every published Life of Research record (all authors; filtered later)."""
    page, records = 1, []
    while True:
        data = get_json(f"{config.LOR_BASE}/api/records", {"page": page})
        records.extend(data.get("records", []))
        per_page = data.get("per_page") or 20
        if page * per_page >= data.get("total", 0) or not data.get("records") or page >= 200:
            return records
        page += 1


# ============ CLEANING ============
# "(c) 2026 Pranay Mahendrakar. Licensed under CC BY 4.0." - the licence ends at
# the first period followed by a space, not at the one inside "4.0".
_COPYRIGHT = re.compile(r"^\s*\(c\)\s*\d{4}.*?\.\s+Licensed under\s.*?\.(?=\s|$)\s*", re.I)
_LOR_HEADER = re.compile(r"^\s*Life of Research\s*", re.I)


def clean_abstract(raw):
    """Plain text from Zenodo's HTML description, minus the copyright/site header."""
    text = re.sub(r"<\s*(br|/p|/div|/li)\s*/?>", "\n", raw or "", flags=re.I)
    text = html.unescape(re.sub(r"<[^>]+>", " ", text))
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\s*\n\s*", "\n", text).strip()
    for _ in range(2):
        text = _COPYRIGHT.sub("", text)
        text = _LOR_HEADER.sub("", text)
    return re.sub(r"\s+", " ", text).strip()


def short_title(title, limit=90):
    """The punchy part of a long academic title: up to the first colon (or '?')."""
    title = re.sub(r"\s+", " ", title or "").strip()
    head = title
    m = re.search(r":\s", title)
    if m and m.start() >= 12:
        head = title[:m.start()]
    else:
        q = title.find("? ")
        if q >= 12:
            head = title[:q + 1]
    if len(head) > limit:
        cut = head[:limit].rsplit(" ", 1)[0].rstrip(",;:-")
        head = cut + "…"
    return head


def norm_title(title):
    return re.sub(r"[^a-z0-9]+", " ", (title or "").lower()).strip()


_REVIEW = re.compile(
    r"review(s|ed|ing)?\s+and\s+synthesi[sz]|synthesi[sz](es|ing|ed)\s+(the\s+)?published"
    r"|reviews?\s+(the\s+)?published|\bthis\s+(review|survey)\b|\ba\s+survey\s+of\b"
    r"|\bsurvey\s+of\s+known\b|\bliterature\s+review\b",
    re.I,
)


def is_review(abstract):
    """True when the paper synthesises published work rather than reporting new experiments."""
    return bool(_REVIEW.search(abstract or ""))


def has_orcid(creators):
    return any((c.get("orcid") or "").strip() == config.ORCID for c in creators or [])


def pdf_file(record):
    files = [f for f in record.get("files") or [] if f.get("key", "").lower().endswith(".pdf")]
    if not files:
        return None
    # The paper is normally the only PDF; if not, the largest one is the paper.
    return max(files, key=lambda f: f.get("size") or 0)


# ============ BUILD ============
def build(zenodo_records, lor_records):
    """Merge the two sources into the ordered posting queue."""
    lor_mine = [r for r in lor_records if has_orcid(r.get("authors"))]
    lor_by_doi = {r["doi"].lower(): r for r in lor_mine if r.get("doi")}
    lor_by_title = {}
    for r in sorted(lor_mine, key=lambda r: r.get("id") or 0):
        lor_by_title[norm_title(r.get("title"))] = r      # later (newer) copies win

    papers, seen_titles, seen_ids = [], set(), set()
    for z in sorted(zenodo_records, key=lambda h: ((h.get("metadata") or {}).get("publication_date") or "", h.get("id") or 0)):
        meta = z.get("metadata") or {}
        title = re.sub(r"\s+", " ", meta.get("title") or "").strip()
        concept = str(z.get("conceptrecid") or z.get("id") or "")
        if not title or not concept or concept in seen_ids:
            continue
        if DUPLICATE_MARK in title.upper():
            continue
        if not has_orcid(meta.get("creators")):
            continue
        rtype = (meta.get("resource_type") or {}).get("type")
        if rtype and rtype != "publication":
            continue
        pdf = pdf_file(z)
        if not pdf:
            continue
        key = norm_title(title)
        if key in seen_titles:
            continue
        seen_titles.add(key)
        seen_ids.add(concept)

        doi = (z.get("doi") or meta.get("doi") or "").strip()
        lor = lor_by_doi.get(doi.lower()) or lor_by_title.get(key)
        abstract = clean_abstract(meta.get("description"))
        lor_url = (lor or {}).get("url") or ""
        papers.append({
            "id": concept,
            "record_id": str(z.get("id")),
            "doi": doi,
            "title": title,
            "short_title": short_title(title),
            "abstract": abstract,
            "keywords": [k.strip() for k in meta.get("keywords") or [] if k and k.strip()],
            "published": meta.get("publication_date") or "",
            "subtype": (meta.get("resource_type") or {}).get("subtype") or "",
            "is_review": is_review(abstract),
            "pdf_url": (pdf.get("links") or {}).get("self") or "",
            "pdf_name": pdf.get("key") or "paper.pdf",
            "pdf_size": pdf.get("size") or 0,
            "zenodo_url": (z.get("links") or {}).get("self_html") or f"https://zenodo.org/records/{z.get('id')}",
            "lor_url": lor_url,
            "lor_id": (lor or {}).get("id"),
            "link": lor_url or (f"https://doi.org/{doi}" if doi else ""),
        })
    return papers


def save(papers, path=None):
    payload = {
        "generated_at": datetime.now(config.IST).isoformat(timespec="seconds"),
        "orcid": config.ORCID,
        "count": len(papers),
        "papers": papers,
    }
    with open(path or config.CATALOG_FILE, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=1, ensure_ascii=False)
        f.write("\n")


def load(path=None):
    try:
        with open(path or config.CATALOG_FILE, encoding="utf-8") as f:
            return json.load(f).get("papers", [])
    except FileNotFoundError:
        return []


def refresh():
    """Fresh catalog from the live sources, or the saved one if they are unreachable."""
    try:
        zenodo = fetch_zenodo()
        try:
            lor = fetch_lor()
        except RuntimeError as e:
            print(f"::warning::Life of Research unreachable ({e}); keeping the links from the last catalog")
            old = {p["id"]: p for p in load()}
            lor = [{"doi": p["doi"], "title": p["title"], "url": p["lor_url"], "id": p.get("lor_id"),
                    "authors": [{"orcid": config.ORCID}]} for p in old.values() if p.get("lor_url")]
        papers = build(zenodo, lor)
        if not papers:
            raise RuntimeError("Zenodo returned no papers for this ORCID")
        save(papers)
        return papers
    except RuntimeError as e:
        papers = load()
        if not papers:
            raise
        print(f"::warning::Could not refresh the catalog ({e}); using the saved one ({len(papers)} papers)")
        return papers


def find(papers, ref):
    """A paper by concept id, record id or DOI."""
    ref = (ref or "").strip().lower().replace("https://doi.org/", "")
    for p in papers:
        if ref in (p["id"], p["record_id"], p["doi"].lower()):
            return p
    return None


def main():
    papers = refresh()
    on_lor = sum(1 for p in papers if p["lor_url"])
    print(f"{len(papers)} papers by ORCID {config.ORCID} ({on_lor} on Life of Research)")
    for p in papers[:3]:
        print(f"  {p['published']}  {p['id']}  {p['short_title']}")
    print("  ...")
    for p in papers[-2:]:
        print(f"  {p['published']}  {p['id']}  {p['short_title']}")


if __name__ == "__main__":
    sys.exit(main())
