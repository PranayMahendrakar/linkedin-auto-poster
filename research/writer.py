"""
The research writer: keeps a week of posts written ahead, by Claude.

    python -m research.writer prepare            # pick papers, fetch their text, write out/context.md
    python -m research.writer append [--dry-run] # check Claude's drafts, save the good ones, push

Between the two steps the workflow runs Claude Code on RESEARCH_RUN.md, which
writes out/drafts/<concept_id>.json for each paper in out/context.md.

Saved drafts live in research/drafts/, one JSON file per paper. You can edit
any of them on GitHub before it goes out at 12:00 IST; the poster re-checks
the draft at posting time and falls back to the template post if it no
longer passes.
"""

import glob
import json
import os
import random
import shutil
import subprocess
import sys
import time
from datetime import datetime

from research import catalog, config, pdfs, state
from research.validate import validate

PAPERS_DIR = os.path.join(config.OUT_DIR, "papers")
OUT_DRAFTS = os.path.join(config.OUT_DIR, "drafts")
PUSH_ATTEMPTS = 4


def set_output(**values):
    path = os.environ.get("GITHUB_OUTPUT")
    if path:
        with open(path, "a", encoding="utf-8") as f:
            for k, v in values.items():
                f.write(f"{k}={v}\n")


def draft_path(paper_id):
    return os.path.join(config.DRAFTS_DIR, f"{paper_id}.json")


def recent_styles(ledger, limit=6):
    posted = sorted(ledger["papers"].values(), key=lambda e: e.get("posted_at") or "")
    styles = [e.get("hook_style") for e in posted if e.get("hook_style") and e.get("hook_style") != "template"]
    for path in sorted(glob.glob(os.path.join(config.DRAFTS_DIR, "*.json")), key=os.path.getmtime):
        try:
            with open(path, encoding="utf-8") as f:
                styles.append(json.load(f).get("hook_style"))
        except (ValueError, OSError):
            pass
    return [s for s in styles if s][-limit:]


def prepare():
    papers = catalog.refresh()
    ledger = state.load_ledger()
    queue = state.upcoming(papers, ledger, state.load_overrides(), config.WRITER_BUFFER)
    todo = [p for p in queue if not os.path.exists(draft_path(p["id"]))][:config.WRITER_MAX_PER_RUN]
    if not todo:
        print(f"The next {len(queue)} papers already have drafts. Nothing to write.")
        set_output(skip="true")
        return

    shutil.rmtree(config.OUT_DIR, ignore_errors=True)
    os.makedirs(PAPERS_DIR)
    os.makedirs(OUT_DRAFTS)
    first_drop = state.next_drop(ledger)
    sections = []
    for paper in todo:
        pdf = pdfs.download(paper, os.path.join(PAPERS_DIR, f"{paper['id']}.pdf"))
        text = pdfs.extract_text(pdf, config.PAPER_TEXT_CHARS)
        with open(os.path.join(PAPERS_DIR, f"{paper['id']}.txt"), "w", encoding="utf-8") as f:
            f.write(text or "(No text could be extracted from this PDF. Work from the abstract only.)")
        os.remove(pdf)
        position = queue.index(paper)
        sections.append("\n".join([
            f"## Paper {len(sections) + 1} of {len(todo)}",
            "",
            f"- concept_id: {paper['id']}",
            f"- Goes out as: Research drop #{first_drop + position:03d} (about {position} day(s) from now)",
            f"- Title: {paper['title']}",
            f"- Published: {paper['published']}",
            f"- DOI: {paper['doi']}",
            f"- Link the post will carry: {paper['link']}",
            f"- Keywords: {', '.join(paper['keywords']) or '(none: choose from the abstract)'}",
            f"- Kind: {'REVIEW / SYNTHESIS of published work. Never describe it as your own experiments.' if paper['is_review'] else 'Original paper.'}",
            f"- Full text: out/papers/{paper['id']}.txt",
            f"- Write to: out/drafts/{paper['id']}.json",
            "",
            "### Abstract",
            "",
            paper["abstract"],
        ]))
    styles = recent_styles(ledger)
    context = "\n".join([
        "# Research writer context",
        "",
        f"Date: {datetime.now(config.IST):%d %B %Y} (posts go out at 12:00 IST, one a day, in this order)",
        f"Recently used hook styles, oldest first (don't repeat the last one; vary them): {', '.join(styles) or 'none yet'}",
        "",
        *[s + "\n" for s in sections],
    ])
    with open(os.path.join(config.OUT_DIR, "context.md"), "w", encoding="utf-8") as f:
        f.write(context)
    print(f"Prepared {len(todo)} paper(s): {', '.join(p['short_title'] for p in todo)}")
    set_output(skip="false", count=str(len(todo)))


def git(*args, check=True):
    r = subprocess.run(["git", *args], cwd=config.REPO, capture_output=True, text=True)
    if check and r.returncode != 0:
        print(f"::error::git {args[0]} failed: {(r.stderr or r.stdout).strip()[:300]}")
        sys.exit(1)
    return r


def append(dry_run):
    papers = {p["id"]: p for p in catalog.load()}
    good, bad = {}, 0
    for path in sorted(glob.glob(os.path.join(OUT_DRAFTS, "*.json"))):
        name = os.path.basename(path)
        try:
            with open(path, encoding="utf-8") as f:
                draft = json.load(f)
        except ValueError as e:
            print(f"✗ {name}: not valid JSON ({e})")
            bad += 1
            continue
        paper = papers.get(str(draft.get("concept_id")))
        if not paper:
            print(f"✗ {name}: concept_id {draft.get('concept_id')!r} is not in the catalog")
            bad += 1
            continue
        text_file = os.path.join(PAPERS_DIR, f"{paper['id']}.txt")
        text = open(text_file, encoding="utf-8").read() if os.path.exists(text_file) else ""
        problems = validate(draft, paper, text)
        if problems:
            bad += 1
            print(f"✗ {paper['short_title']}:")
            for p in problems:
                print(f"    - {p}")
            continue
        draft["written_at"] = datetime.now(config.IST).isoformat(timespec="seconds")
        good[paper["id"]] = draft
        print(f"✓ {paper['short_title']}: [{draft['hook_style']}] {draft['body'].splitlines()[0]}")

    if not good:
        print(f"::error::No draft passed the checks ({bad} rejected). The poster will use template posts until one does.")
        sys.exit(1)

    branch = os.environ.get("GITHUB_REF_NAME") or git("rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    for attempt in range(1, PUSH_ATTEMPTS + 1):
        git("fetch", "--quiet", "origin", branch)
        git("reset", "--quiet", "--hard", f"origin/{branch}")
        os.makedirs(config.DRAFTS_DIR, exist_ok=True)
        changed = []
        for pid, draft in good.items():
            with open(draft_path(pid), "w", encoding="utf-8") as f:
                json.dump(draft, f, indent=1, ensure_ascii=False)
                f.write("\n")
            changed.append(os.path.relpath(draft_path(pid), config.REPO))
        git("add", *changed)
        if git("diff", "--staged", "--quiet", check=False).returncode == 0:
            print("Nothing new to commit.")
            return
        message = f"Research drafts: {', '.join(papers[p]['short_title'][:40] for p in good)}"
        git("commit", "--quiet", "-m", message)
        r = git("push", "--quiet", *(["--dry-run"] if dry_run else []), "origin", f"HEAD:{branch}", check=False)
        if r.returncode == 0:
            print(f"{'DRY RUN, not pushed' if dry_run else 'Pushed'}: {message}")
            if bad:
                print(f"::warning::{bad} draft(s) were rejected; those papers will be retried on the next run")
            return
        print(f"Push attempt {attempt} rejected; retrying from the latest remote state")
        time.sleep(5 * attempt + random.uniform(0, 3))
    print(f"::error::Could not push after {PUSH_ATTEMPTS} attempts")
    sys.exit(1)


def main():
    command = sys.argv[1] if len(sys.argv) > 1 else ""
    if command == "prepare":
        prepare()
    elif command == "append":
        append(dry_run="--dry-run" in sys.argv[2:])
    else:
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
