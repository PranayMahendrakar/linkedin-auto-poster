"""
Decide whether a research post is due now, by the clock in India.

GitHub starts scheduled runs late and sometimes skips them, so the workflow
wakes every 20 minutes through the afternoon and asks this script:

    12:00-17:30 IST  daily paper, once a day, on the first wake-up after 12:00
    16:00-17:45 IST  Sunday "this week's papers" roundup, once a week

Neither goes out within an hour of another post (this module's or the news
poster's), so two posts never compete in the feed. A missed slot is skipped,
not sent at night.

    python -m research.schedule      # prints the decision; sets step output mode=daily|digest|none

Standard library only, so it runs before anything is installed.
"""

import json
import os
from datetime import datetime, timedelta, timezone

from research import config, state


def parse_time(value):
    if not value:
        return None
    try:
        moment = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def news_last_posted(path=None):
    try:
        with open(path or config.NEWS_PROGRESS_FILE, encoding="utf-8") as f:
            return parse_time(json.load(f).get("last_posted_at"))
    except (FileNotFoundError, ValueError):
        return None


def decide(now, ledger, news_last=None):
    """('daily' | 'digest' | None, reason)."""
    now = now.astimezone(config.IST)
    t, today = now.time(), now.date()

    daily_open = config.DAILY_SLOT <= t < config.DAILY_LAST_CALL
    daily_due = daily_open and ledger.get("last_daily_date") != today.isoformat()
    digest_open = now.weekday() == config.DIGEST_WEEKDAY and config.DIGEST_SLOT <= t < config.DIGEST_LAST_CALL
    digest_due = digest_open and state.week_key(today) not in ledger.get("digests", {})

    if not (daily_due or digest_due):
        if ledger.get("last_daily_date") == today.isoformat() and not digest_open:
            return None, "today's paper is already posted"
        return None, f"no research slot open at {now:%H:%M} IST"

    gap = timedelta(minutes=config.MIN_GAP_MINUTES)
    for label, moment in (("a research post", parse_time(ledger.get("last_posted_at"))),
                          ("a news post", news_last)):
        if moment and timedelta(0) <= now - moment < gap:
            wait = gap - (now - moment)
            return None, f"{label} went out at {moment.astimezone(config.IST):%H:%M} IST; waiting {int(wait.total_seconds() // 60) + 1} more min"

    if daily_due:
        return "daily", f"daily paper due ({now:%H:%M} IST)"
    return "digest", f"Sunday roundup due ({now:%H:%M} IST)"


def main():
    mode, reason = decide(datetime.now(config.IST), state.load_ledger(), news_last_posted())
    print(reason)
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a", encoding="utf-8") as f:
            f.write(f"mode={mode or 'none'}\n")


if __name__ == "__main__":
    main()
