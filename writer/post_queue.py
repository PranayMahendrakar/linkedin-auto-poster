"""
Queue helper for the LinkedIn writer (.github/workflows/writer.yml).

    python writer/post_queue.py prepare             # before the Claude run
    python writer/post_queue.py append [--dry-run]  # after it

prepare  Reads linkedin_posts.csv and post_progress.json, writes
         writer/out/context.md for Claude, copies the newest thumbnails to
         writer/out/examples/, and sets the step outputs `buffer` (posts
         waiting) and `skip` (true once MAX_BUFFER are waiting).

append   Checks writer/out/post.json and the thumbnails, appends the post to
         linkedin_posts.csv with the next post_number, copies its thumbnail to
         images/<post_number>.svg, then commits and pushes. The site's daily
         writer and the poster push to this repo too, so a rejected push starts
         again from the latest remote state instead of merging.
         --dry-run does everything except the push (it still checks the push
         would be allowed).

Exit codes: 0 ok, 1 failure.
"""

import csv
import json
import os
import random
import re
import shutil
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "writer" / "out"
CSV_FILE = ROOT / "linkedin_posts.csv"
PROGRESS_FILE = ROOT / "post_progress.json"
IMAGES = ROOT / "images"

MAX_BUFFER = int(os.environ.get("MAX_BUFFER", "30"))
RECENT = 30              # recent topics shown to Claude, and checked for repeats
BACKFILL = 2             # queued posts per run that get a missing thumbnail
EXAMPLES = 3             # newest thumbnails shown to Claude as the house style
MAX_SVG_BYTES = 30_000
PUSH_ATTEMPTS = 4
IST = timezone(timedelta(hours=5, minutes=30))
SVG_NS = "{http://www.w3.org/2000/svg}"
FORBIDDEN_TAGS = {"script", "foreignObject", "image", "iframe", "object", "embed",
                  "audio", "video"}


def fail(message):
    print(f"::error::{message}")
    sys.exit(1)


def set_output(**values):
    path = os.environ.get("GITHUB_OUTPUT")
    if path:
        with open(path, "a", encoding="utf-8") as f:
            for key, value in values.items():
                f.write(f"{key}={value}\n")


def read_posts():
    with CSV_FILE.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        return reader.fieldnames, list(reader)


def next_index():
    if PROGRESS_FILE.exists():
        return json.loads(PROGRESS_FILE.read_text(encoding="utf-8")).get("next_index", 0)
    return 0


def first_line(text):
    return next((line.strip() for line in text.splitlines() if line.strip()), "")


def norm(topic):
    return re.sub(r"[^a-z0-9]", "", topic.lower())


# ============ PREPARE ============
def prepare():
    # A post.json left over from an earlier run would be appended as if it
    # were this run's work.
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True)

    _, rows = read_posts()
    done = min(next_index(), len(rows))
    waiting = rows[done:]
    skip = len(waiting) >= MAX_BUFFER
    set_output(buffer=len(waiting), skip=str(skip).lower())
    print(f"Queue: {len(rows)} posts, {done} published, {len(waiting)} waiting (cap {MAX_BUFFER}).")
    if skip:
        print("Queue is full. Nothing to write this run.")
        return

    need_thumb = [r for r in waiting
                  if r["post_number"].strip()
                  and not (IMAGES / f"{r['post_number'].strip()}.svg").exists()][:BACKFILL]

    now = datetime.now(IST)
    lines = [
        "# Context for this run",
        "",
        f"- Date: {now:%A %d %B %Y}, {now:%H:%M} IST",
        f"- Slot: {os.environ.get('SLOT') or 'any current AI news'}",
        f"- Queue: {len(waiting)} posts waiting, so this one goes live in about "
        f"{len(waiting) // 2 + 1} day(s).",
        "",
        "## Topics already in the queue or posted recently (newest last)",
        "",
    ]
    lines += [f"- #{r['post_number']} {r['category']}: {first_line(r['post_text'])}"
              for r in rows[-RECENT:]]
    lines += ["", "## Needs a thumbnail", ""]
    for r in need_thumb:
        lines += [f"### Post {r['post_number'].strip()}: {r['category']}", "",
                  r["post_text"].strip(), ""]
    if not need_thumb:
        lines.append("None.")
    (OUT / "context.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Wrote writer/out/context.md ({len(need_thumb)} thumbnail(s) to backfill).")

    # The newest thumbnails, as house-style examples for this run.
    examples = sorted((p for p in IMAGES.glob("*.svg") if p.stem.isdigit()),
                      key=lambda p: int(p.stem))[-EXAMPLES:]
    (OUT / "examples").mkdir()
    for path in examples:
        shutil.copyfile(path, OUT / "examples" / path.name)
    print(f"Copied {len(examples)} example thumbnail(s) to writer/out/examples/.")


# ============ CHECKS ============
def svg_problems(path):
    """Reasons this SVG can't be used. An empty list means it's fine."""
    data = path.read_bytes()
    if len(data) > MAX_SVG_BYTES:
        return [f"{len(data)} bytes, over the {MAX_SVG_BYTES} limit"]
    if b"<!DOCTYPE" in data or b"<!ENTITY" in data:
        return ["DOCTYPE/ENTITY declarations are not allowed"]
    try:
        root = ET.fromstring(data)
    except ET.ParseError as e:
        return [f"not valid XML: {e}"]

    problems = set()
    if root.tag != f"{SVG_NS}svg":
        problems.add("root element is not <svg> in the SVG namespace")
    if root.get("viewBox", "").split() != ["0", "0", "1080", "1080"]:
        problems.add('viewBox is not "0 0 1080 1080"')
    external_url = re.compile(r"url\(\s*['\"]?(?!#)")
    for el in root.iter():
        tag = el.tag.rsplit("}", 1)[-1]
        if tag in FORBIDDEN_TAGS:
            problems.add(f"<{tag}> is not allowed")
        if tag == "style" and el.text and ("@import" in el.text or external_url.search(el.text)):
            problems.add("external reference in <style>")
        for name, value in el.attrib.items():
            attr = name.rsplit("}", 1)[-1]
            if attr.lower().startswith("on"):
                problems.add(f"event attribute {attr}=")
            if attr == "href" and not value.startswith("#"):
                problems.add(f"external href {value[:60]}")
            if external_url.search(value):
                problems.add(f"external url() in {attr}=")
    if not any(el.tag == f"{SVG_NS}text" for el in root.iter()):
        problems.add("no <text> in the thumbnail")
    return sorted(problems)


def render_error(path):
    """Render with rsvg-convert, the tool the poster uses. None means it worked."""
    if not shutil.which("rsvg-convert"):
        print("  rsvg-convert not installed; render check skipped")
        return None
    png = path.with_suffix(".check.png")
    r = subprocess.run(["rsvg-convert", "-w", "1080", "-h", "1080", "-f", "png",
                        "-o", str(png), str(path)],
                       capture_output=True, text=True, timeout=60)
    if r.returncode != 0 or not png.exists() or png.stat().st_size < 2000:
        return "rsvg-convert failed: " + (r.stderr.strip()[:200] or "image came out empty")
    return None


def thumbnail_ok(path):
    problems = svg_problems(path)
    if not problems:
        error = render_error(path)
        problems = [error] if error else []
    if problems:
        print(f"WARNING: {path.name} rejected: {'; '.join(problems)}")
        return False
    print(f"Thumbnail {path.name} OK ({path.stat().st_size} bytes)")
    return True


def load_post():
    path = OUT / "post.json"
    if not path.exists():
        fail("writer/out/post.json was never written")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as e:
        fail(f"writer/out/post.json is not valid JSON: {e}")

    topic = " ".join(str(data.get("topic") or "").split())
    text = str(data.get("linkedin_text") or "").strip()
    hashtags = " ".join(str(data.get("hashtags") or "").split())
    sources = {s for s in data.get("sources") or []
               if isinstance(s, str) and s.startswith(("https://", "http://"))}

    problems = []
    if not 2 <= len(topic) <= 60:
        problems.append(f'topic "{topic}" should be 2-60 characters')
    words = len(text.split())
    if not 100 <= words <= 350:
        problems.append(f"post is {words} words (brief asks for 150-260)")
    tags = hashtags.split()
    if not 3 <= len(tags) <= 6 or not all(re.fullmatch(r"#\w+", t) for t in tags):
        problems.append(f'hashtags "{hashtags}" should be 3-6 tags like #AI')
    last_line = text.splitlines()[-1] if text else ""
    if last_line and all(w.startswith("#") for w in last_line.split()):
        problems.append("the post text ends with a hashtag line (hashtags go in their own field)")
    if len(sources) < 2:
        problems.append("fewer than two source URLs")
    if problems:
        fail("post.json rejected: " + "; ".join(problems))
    return topic, text, hashtags


# ============ APPEND ============
def git(*args, check=True):
    r = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True)
    if check and r.returncode != 0:
        fail(f"git {args[0]} failed: {(r.stderr or r.stdout).strip()[:300]}")
    return r


def append_row(fields, row):
    data = CSV_FILE.read_bytes()
    with CSV_FILE.open("a", encoding="utf-8", newline="") as f:
        if data and not data.endswith(b"\n"):
            f.write("\r\n")
        csv.DictWriter(f, fieldnames=fields).writerow(row)


def append(dry_run):
    topic, text, hashtags = load_post()
    thumb = OUT / "thumbnail.svg"
    if not thumb.exists():
        print("WARNING: no thumbnail.svg written; the post will go out text-only.")
    use_thumb = thumb.exists() and thumbnail_ok(thumb)
    backfill = {}
    for path in sorted(OUT.glob("thumb_*.svg")):
        number = path.stem.split("_", 1)[1]
        if number.isdigit() and thumbnail_ok(path):
            backfill[number] = path

    branch = os.environ.get("GITHUB_REF_NAME") or git("rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    for attempt in range(1, PUSH_ATTEMPTS + 1):
        git("fetch", "--quiet", "origin", branch)
        git("reset", "--quiet", "--hard", f"origin/{branch}")

        fields, rows = read_posts()
        if norm(topic) in {norm(r["category"]) for r in rows[-RECENT * 2:]}:
            fail(f'topic "{topic}" is already in the queue')
        numbers = [int(r["post_number"]) for r in rows if r["post_number"].strip().isdigit()]
        number = max(numbers, default=0) + 1

        append_row(fields, {"post_number": number, "category": topic,
                            "post_text": text, "hashtags": hashtags})
        changed = ["linkedin_posts.csv"]
        IMAGES.mkdir(exist_ok=True)
        if use_thumb:
            shutil.copyfile(thumb, IMAGES / f"{number}.svg")
            changed.append(f"images/{number}.svg")
        queued = {r["post_number"].strip() for r in rows}
        filled = []
        for n, path in backfill.items():
            if n in queued and not (IMAGES / f"{n}.svg").exists():
                shutil.copyfile(path, IMAGES / f"{n}.svg")
                changed.append(f"images/{n}.svg")
                filled.append(f"#{n}")

        message = f"Add #{number}: {topic}"
        if filled:
            message += f" (+ thumbnails for {', '.join(filled)})"
        git("add", *changed)
        git("commit", "--quiet", "-m", message)

        push = ["push", "--quiet", *(["--dry-run"] if dry_run else []), "origin", f"HEAD:{branch}"]
        r = git(*push, check=False)
        if r.returncode == 0:
            prefix = "DRY RUN, not pushed:" if dry_run else "Pushed:"
            print(f"{prefix} {message}")
            return
        reason = (r.stderr or r.stdout).strip().splitlines()
        print(f"Push attempt {attempt} rejected ({reason[-1][:160] if reason else 'no output'}); "
              "starting again from the latest remote state")
        time.sleep(5 * attempt + random.uniform(0, 3))
    fail(f"could not push after {PUSH_ATTEMPTS} attempts")


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
