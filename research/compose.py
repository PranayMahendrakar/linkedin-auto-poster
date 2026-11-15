"""
Turn a paper plus its draft into the exact LinkedIn post text.

A draft comes from the research writer (research/drafts/<id>.json, written by
Claude) or, if there is none or it fails the checks, from template_draft()
here, so a day is never missed.

The post is assembled in a fixed shape:

    <body: hook line, then short paragraphs>

    Read it on Life of Research:
    <link>
    DOI: <doi>

    <closing question>

    <hashtags>

LinkedIn's Posts API reads commentary as "little text": the characters
| { } @ [ ] ( ) < > # \\ * _ ~ are markup and must be backslash-escaped, or the
post is cut short or rejected. Hashtags go in as {hashtag|\\#|Tag} templates.
"""

import hashlib
import re
from datetime import date

from research import config

RESERVED = set("|{}@[]()<>#\\*_~")
HASHTAG_RE = re.compile(r"^#[A-Za-z][A-Za-z0-9]{1,39}$")
HOOK_STYLES = ("number_gap", "contrast", "everyday_example", "myth_buster", "builder_warning", "template")


def escape_little(text):
    return "".join("\\" + ch if ch in RESERVED else ch for ch in text)


def hashtag_markup(tag):
    return "{hashtag|\\#|" + tag.lstrip("#") + "}"


def to_hashtag(phrase):
    """'ECG biometrics' -> '#ECGBiometrics'; None if it can't make a clean tag."""
    words = re.findall(r"[A-Za-z0-9]+", phrase or "")
    if not words:
        return None
    tag = "#" + "".join(w if w.isupper() else w[:1].upper() + w[1:] for w in words)
    return tag if HASHTAG_RE.match(tag) and len(tag) <= 31 else None


def normalize_hashtags(tags):
    """The paper's topic tags (deduped, valid, at most MAX_TOPIC_HASHTAGS), then the fixed research tags."""
    fixed = {t.lower() for t in config.FIXED_HASHTAGS}
    out, seen = [], set()
    for tag in list(tags or []):
        tag = tag.strip()
        if not tag.startswith("#"):
            tag = "#" + tag
        if HASHTAG_RE.match(tag) and tag.lower() not in seen and tag.lower() not in fixed:
            out.append(tag)
            seen.add(tag.lower())
    return out[:config.MAX_TOPIC_HASHTAGS] + list(config.FIXED_HASHTAGS)


def month_year(iso_day):
    try:
        return date.fromisoformat(iso_day).strftime("%B %Y")
    except (TypeError, ValueError):
        return ""


def link_block(paper):
    lines = []
    if paper.get("lor_url"):
        lines += ["Read it on Life of Research:", paper["lor_url"]]
    elif paper.get("doi"):
        lines += ["Read it online:", f"https://doi.org/{paper['doi']}"]
    if paper.get("doi") and paper.get("lor_url"):
        lines.append(f"DOI: {paper['doi']}")
    return "\n".join(lines)


def assemble(paper, draft):
    """(preview, commentary): the post as people will read it, and the escaped API string."""
    body = draft["body"].strip()
    question = (draft.get("question") or "").strip()
    tags = normalize_hashtags(draft.get("hashtags"))

    parts = [body, link_block(paper)]
    if question:
        parts.append(question)
    preview = "\n\n".join(p for p in parts if p) + "\n\n" + " ".join(tags)
    commentary = "\n\n".join(escape_little(p) for p in parts if p) + "\n\n" + " ".join(hashtag_markup(t) for t in tags)
    return preview, commentary


# ============ TEMPLATE FALLBACK ============
OPENERS = (
    "A paper I wrote, published in {when}.",
    "From my research archive, published in {when}.",
    "I published this paper in {when}. Here is what it is about.",
    "Sharing one of my papers, published in {when}.",
)


def sentences(text):
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(])", text or "") if s.strip()]


def excerpt(text, max_chars):
    out = ""
    for s in sentences(text):
        if len(out) + len(s) + 1 > max_chars:
            break
        out = (out + " " + s).strip()
    if not out:                                   # one enormous sentence
        out = (text or "")[:max_chars].rsplit(" ", 1)[0].rstrip(",;:") + "…"
    return out


def pick(options, seed):
    digest = hashlib.sha256(str(seed).encode()).digest()
    return options[digest[0] % len(options)]


def template_draft(paper, drop):
    """A plain, safe post built only from the paper's own metadata."""
    when = month_year(paper["published"]) or "my archive"
    opener = pick(OPENERS, paper["id"]).format(when=when)
    head = f"{opener}\n\n{paper['title']}"
    fixed = len(head) + len(link_block(paper)) + 200            # hashtags and spacing
    abstract = excerpt(paper["abstract"], max(300, min(1300, config.COMMENTARY_BUDGET - fixed)))
    tags = ["#AI"] + [t for t in (to_hashtag(k) for k in paper.get("keywords") or []) if t][:3]
    return {
        "concept_id": paper["id"],
        "hook_style": "template",
        "body": f"{head}\n\n{abstract}\n\nThe full paper is attached below.",
        "question": "",
        "hashtags": tags,
    }
