"""
Checks every Claude-written draft must pass before it can go live. They run
twice: when the writer saves a draft, and again at posting time. A draft that
fails at posting time is replaced by the template post, never sent.

What they protect:
  * Credibility. Every number in the post must appear in the paper itself,
    and a paper that reviews published work can't be described as the
    author's own experiments.
  * Reach. No engagement bait, no links or hashtags in the body (the module
    adds them), no emoji, a hook short enough to show before "...see more".
  * Fit. LinkedIn's 3,000-character limit.
"""

import re
import unicodedata

from research import config
from research.compose import HASHTAG_RE, HOOK_STYLES, assemble

BANNED = (
    "excited to share", "thrilled to", "delighted to", "happy to announce", "proud to announce",
    "game-changer", "game changer", "groundbreaking", "ground-breaking", "revolutionary",
    "mind-blowing", "unlock", "delve", "in today's fast-paced", "comment yes", "comment \"yes\"",
    "comment 'yes'", "drop a comment", "link in comments", "link in the comments",
    "link in first comment", "link in the first comment", "dm me", "like if", "repost if",
    "share if", "follow me for", "agree?",
)
OWN_EXPERIMENT = re.compile(
    r"\b(I|we)\s+(ran|trained|fine-tuned|tested|measured|benchmarked|collected|surveyed|recruited|built a benchmark|conducted)\b"
    r"|\bour\s+(experiments?|participants|dataset we collected|lab)\b|\bmy\s+experiments?\b",
    re.I,
)
NUMBER = re.compile(r"(?<![A-Za-z])\d[\d,]*(?:\.\d+)?")
FREE_NUMBER_MAX = 12          # small counts ("three", "6 controls") are fine without proof


def numbers_in(text):
    return {n.replace(",", "").rstrip(".") for n in NUMBER.findall(text or "")}


def is_emoji(ch):
    cp = ord(ch)
    return (cp >= 0x1F000 or 0x2600 <= cp <= 0x27BF or 0xFE00 <= cp <= 0xFE0F
            or (unicodedata.category(ch) == "So" and cp > 0x2BFF))


def validate(draft, paper, paper_text=""):
    """List of problems; empty means the draft may go live."""
    errors = []
    if not isinstance(draft, dict):
        return ["draft is not a JSON object"]
    if str(draft.get("concept_id")) != paper["id"]:
        errors.append(f"concept_id {draft.get('concept_id')!r} does not match paper {paper['id']}")

    body = (draft.get("body") or "").strip()
    question = (draft.get("question") or "").strip()
    if not body:
        return errors + ["body is empty"]
    hook = body.splitlines()[0].strip()
    if len(hook) > 120:
        errors.append(f"hook is {len(hook)} characters; keep the first line under 110")
    words = len(body.split())
    if not 90 <= words <= 280:
        errors.append(f"body is {words} words; aim for 150-230")
    if draft.get("hook_style") not in HOOK_STYLES or draft.get("hook_style") == "template":
        errors.append(f"hook_style must be one of {', '.join(HOOK_STYLES[:-1])}")
    if not question.endswith("?") or len(question) > 180:
        errors.append("question must be one sentence ending in '?' (under 180 characters)")

    text = f"{body}\n{question}"
    lowered = text.lower()
    for phrase in BANNED:
        if phrase in lowered:
            errors.append(f"banned phrase: {phrase!r}")
    if re.search(r"https?://|www\.|lifeofresearch\.com|doi\.org", text, re.I):
        errors.append("no links in the body; the module adds the paper link")
    if "#" in text:
        errors.append("no hashtags in the body; put them in the hashtags list")
    if "*" in text:
        errors.append("no markdown in the body; LinkedIn shows asterisks literally")
    emoji = sorted({ch for ch in text if is_emoji(ch)})
    if emoji:
        errors.append(f"no emoji: {' '.join(emoji)}")
    if paper.get("is_review") and OWN_EXPERIMENT.search(text):
        errors.append("this paper reviews published work; don't describe it as your own experiments")

    source = numbers_in(f"{paper['title']} {paper['abstract']} {paper.get('published', '')} {paper_text}")
    unproven = sorted(n for n in numbers_in(text) if n not in source and not (n.isdigit() and int(n) <= FREE_NUMBER_MAX))
    if unproven:
        errors.append(f"numbers not found in the paper: {', '.join(unproven)}")

    tags = draft.get("hashtags") or []
    if not isinstance(tags, list) or not 3 <= len(tags) <= 5:
        errors.append("hashtags: give 3 to 5")
    else:
        bad = [t for t in tags if not HASHTAG_RE.match(str(t))]
        if bad:
            errors.append(f"invalid hashtags: {', '.join(map(str, bad))}")

    if not errors:
        preview, _ = assemble(paper, draft)
        if len(preview) > config.COMMENTARY_BUDGET:
            errors.append(f"post is {len(preview)} characters with link and hashtags; the limit is {config.COMMENTARY_BUDGET}")
    return errors
