"""
What has been posted (research/posted.json) and what you asked for by hand
(research/overrides.json). Standard library only: the schedule check imports
this before anything is installed.

Progress is a ledger keyed by Zenodo concept id, not a position in a list, so
new papers arriving, a paper being re-versioned or the order changing can
never cause a repeat or a skip.
"""

import json
import os
from datetime import datetime

from research import config


# ============ LEDGER ============
def empty_ledger():
    return {"papers": {}, "digests": {}, "last_daily_date": None, "last_posted_at": None}


def load_ledger(path=None):
    path = path or config.LEDGER_FILE
    if not os.path.exists(path):
        return empty_ledger()
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    base = empty_ledger()
    base.update(data)
    return base


def save_ledger(ledger, path=None):
    with open(path or config.LEDGER_FILE, "w", encoding="utf-8") as f:
        json.dump(ledger, f, indent=1, ensure_ascii=False)
        f.write("\n")


def next_drop(ledger):
    """The series number of the next research post: Research drop #001, #002, ..."""
    return len(ledger["papers"]) + 1


def now_ist():
    return datetime.now(config.IST)


def record_paper(ledger, paper, post_urn, source, hook_style, when=None):
    when = when or now_ist()
    ledger["papers"][paper["id"]] = {
        "drop": next_drop(ledger),
        "doi": paper["doi"],
        "title": paper["short_title"],
        "posted_at": when.isoformat(timespec="seconds"),
        "post_urn": post_urn,
        "source": source,
        "hook_style": hook_style,
    }
    ledger["last_daily_date"] = when.date().isoformat()
    ledger["last_posted_at"] = when.isoformat(timespec="seconds")


def record_digest(ledger, week, post_urn, paper_ids, when=None, skipped=None):
    when = when or now_ist()
    entry = {"posted_at": when.isoformat(timespec="seconds"), "post_urn": post_urn, "papers": paper_ids}
    if skipped:
        entry = {"skipped": skipped, "checked_at": when.isoformat(timespec="seconds")}
    ledger["digests"][week] = entry
    if post_urn:
        ledger["last_posted_at"] = when.isoformat(timespec="seconds")


def week_key(day):
    year, week, _ = day.isocalendar()
    return f"{year}-W{week:02d}"


# ============ OVERRIDES ============
def load_overrides(path=None):
    path = path or config.OVERRIDES_FILE
    data = {"skip": [], "priority": []}
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            data.update(json.load(f))
    return data


def _refs(paper):
    return {paper["id"], paper["record_id"], paper["doi"].lower()}


def _matches(paper, ref):
    return str(ref).strip().lower().replace("https://doi.org/", "") in _refs(paper)


def upcoming(papers, ledger, overrides, limit=None):
    """Unposted papers in posting order: priority list first, then oldest first."""
    posted = set(ledger["papers"])
    skip = overrides.get("skip") or []
    pool = [p for p in papers if p["id"] not in posted and not any(_matches(p, s) for s in skip)]
    ordered = []
    for ref in overrides.get("priority") or []:
        for p in pool:
            if _matches(p, ref) and p not in ordered:
                ordered.append(p)
    ordered += [p for p in pool if p not in ordered]
    return ordered[:limit] if limit else ordered
