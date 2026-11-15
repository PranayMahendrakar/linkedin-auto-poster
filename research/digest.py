"""
The Sunday "this week's papers" roundup: a text post listing every paper
published Monday-Sunday of the current week, each with its link.

New papers arrive about two a day while the daily post works through the
archive one a day, so without this a new paper would wait months to be seen.
"""

from datetime import date, timedelta

from research import config
from research.compose import escape_little, hashtag_markup, normalize_hashtags

TAGS = ["#AI", "#MachineLearning"]          # the fixed research tags are added after these


def week_range(day):
    monday = day - timedelta(days=day.weekday())
    return monday, monday + timedelta(days=6)


def week_label(day):
    monday, sunday = week_range(day)
    if monday.month == sunday.month:
        return f"{monday.day}-{sunday.day} {sunday:%b %Y}"
    return f"{monday.day} {monday:%b} - {sunday.day} {sunday:%b %Y}"


def week_papers(papers, day):
    monday, _ = week_range(day)
    out = []
    for p in papers:
        try:
            published = date.fromisoformat(p["published"])
        except (TypeError, ValueError):
            continue
        if monday <= published <= day:
            out.append(p)
    return out


def compose(papers, day):
    """(preview, commentary) for the roundup post."""
    n = len(papers)
    opener = (f"{n} papers in 7 days. Here is every one, with a link." if n > 1
              else "1 new paper this week. Here it is.")
    closing = "Every one is free to read, with a permanent DOI."
    question = "Which one should I go deeper on?" if n > 1 else "What should I write about next?"
    tags = normalize_hashtags(TAGS)

    def build(with_links):
        if with_links:
            items = "\n\n".join(f"→ {p['short_title']}\n{p['link']}" for p in papers)
            return [opener, items, closing, question]
        items = "\n".join(f"→ {p['short_title']}" for p in papers)
        return [opener, items, f"All of them: {config.LOR_BASE}", closing, question]

    parts = build(True)
    if len("\n\n".join(parts)) + 120 > config.COMMENTARY_BUDGET:
        parts = build(False)
    preview = "\n\n".join(parts) + "\n\n" + " ".join(tags)
    commentary = "\n\n".join(escape_little(x) for x in parts) + "\n\n" + " ".join(hashtag_markup(t) for t in tags)
    return preview, commentary
