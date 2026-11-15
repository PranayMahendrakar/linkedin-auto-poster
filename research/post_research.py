"""
Post the next research paper (or the Sunday roundup) to LinkedIn.

    python -m research.post_research                      # next paper
    python -m research.post_research --dry-run            # build everything, post nothing
    python -m research.post_research --paper 14040452     # a specific paper (concept id, record id or DOI)
    python -m research.post_research --mode digest        # this week's roundup

A paper post is the written text with the paper's own PDF attached as a
LinkedIn document, exactly as published: people page through the real paper
in the feed. The Sunday roundup is a text post with a link to each paper.

Every run leaves its output in research/out/: preview.md (the exact post text)
and paper.pdf (what LinkedIn receives). The workflow uploads that folder as an
artifact, so a dry run shows you exactly what would go out.
"""

import argparse
import json
import os
import shutil
import sys
from datetime import datetime

from research import catalog, compose, config, digest, linkedin_rest, pdfs, state
from research.validate import validate


def fail(message):
    print(f"::error::{message}")
    sys.exit(1)


def fresh_out_dir():
    shutil.rmtree(config.OUT_DIR, ignore_errors=True)
    os.makedirs(config.OUT_DIR, exist_ok=True)


def write_preview(lines, text):
    with open(os.path.join(config.OUT_DIR, "preview.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n\n---\n\n" + text + "\n")
    print("\n".join(lines))
    print("-" * 60)
    print(text)
    print("-" * 60)


def load_draft(paper):
    path = os.path.join(config.DRAFTS_DIR, f"{paper['id']}.json")
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except ValueError as e:
        print(f"::warning::{path} is not valid JSON ({e}); using the template post")
        return None


def publish(action):
    try:
        return action()
    except linkedin_rest.TokenRejected as e:
        fail(str(e))
    except linkedin_rest.LinkedInError as e:
        fail(f"Nothing was posted: {e}")


def run_daily(args):
    papers = catalog.refresh()
    ledger = state.load_ledger()
    if args.paper:
        paper = catalog.find(papers, args.paper)
        if not paper:
            fail(f"No paper {args.paper!r} in the catalog")
        if paper["id"] in ledger["papers"] and not args.dry_run:
            fail(f"{paper['short_title']!r} was already posted on {ledger['papers'][paper['id']]['posted_at']}")
    else:
        queue = state.upcoming(papers, ledger, state.load_overrides(), 1)
        if not queue:
            print("::warning::Every paper has been posted. Nothing to do until a new one is published.")
            return
        paper = queue[0]

    drop = state.next_drop(ledger)
    fresh_out_dir()
    print(f"Research post #{drop}: {paper['short_title']} ({paper['published']}, {paper['doi']})")
    paper_pdf = pdfs.download(paper, os.path.join(config.OUT_DIR, "paper.pdf"))
    paper_text = pdfs.extract_text(paper_pdf)
    upload, pages = pdfs.within_page_limit(paper_pdf, os.path.join(config.OUT_DIR, "paper_300.pdf"))

    draft, source = load_draft(paper), "writer"
    if draft:
        problems = validate(draft, paper, paper_text)
        if problems:
            print("::warning::The written draft failed its checks, so the template post is used instead:")
            for p in problems:
                print(f"  - {p}")
            draft = None
    if not draft:
        draft, source = compose.template_draft(paper, drop), "template"

    preview, commentary = compose.assemble(paper, draft)
    if len(preview) > config.COMMENTARY_BUDGET:
        fail(f"The post is {len(preview)} characters, over the {config.COMMENTARY_BUDGET} budget")
    write_preview([
        f"Research post #{drop}",
        f"Paper:   {paper['title']}",
        f"Link:    {paper['link']}",
        f"Source:  {source} ({draft.get('hook_style')})",
        f"Length:  {len(preview)} characters",
        f"PDF:     {os.path.basename(upload)}, {pages or '?'} pages, {os.path.getsize(upload) // 1024} KB",
    ], preview)

    if args.dry_run:
        print("DRY RUN: nothing was posted and nothing was recorded.")
        return
    urn = publish(lambda: linkedin_rest.publish_document_post(commentary, upload, paper["title"]))
    state.record_paper(ledger, paper, urn, source, draft.get("hook_style"))
    state.save_ledger(ledger)
    print(f"Posted research post #{drop}. {len(state.upcoming(papers, ledger, state.load_overrides()))} papers still to go.")


def run_digest(args):
    papers = catalog.refresh()
    ledger = state.load_ledger()
    today = datetime.now(config.IST).date()
    week = state.week_key(today)
    if (ledger["digests"].get(week) or {}).get("post_urn") and not args.dry_run:
        print(f"The roundup for {week} was already posted.")
        return
    items = digest.week_papers(papers, today)
    if not items:
        print(f"No papers were published in {week}; no roundup this week.")
        if not args.dry_run:
            state.record_digest(ledger, week, None, [], skipped="no papers this week")
            state.save_ledger(ledger)
        return

    fresh_out_dir()
    preview, commentary = digest.compose(items, today)
    write_preview([f"Weekly roundup {week} ({digest.week_label(today)})", f"Papers:  {len(items)}",
                   f"Length:  {len(preview)} characters"], preview)
    if args.dry_run:
        print("DRY RUN: nothing was posted and nothing was recorded.")
        return
    urn = publish(lambda: linkedin_rest.publish_text_post(commentary))
    state.record_digest(ledger, week, urn, [p["id"] for p in items])
    state.save_ledger(ledger)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--mode", choices=["daily", "digest"], default="daily")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--paper", default="", help="concept id, record id or DOI (daily mode)")
    args = parser.parse_args(argv)
    if args.mode == "digest":
        run_digest(args)
    else:
        run_daily(args)


if __name__ == "__main__":
    main()
