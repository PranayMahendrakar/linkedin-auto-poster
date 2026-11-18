"""
Decide whether a research post is due now, by the clock in India.

GitHub runs only 3-5 of a workflow's 72 daily scheduled wake-ups, often hours
late. So the workflow wakes every 20 minutes all day and after the repo's
other workflows finish, and asks this script, whose windows are long enough
that some wake-up always lands in them:

    07:00-midnight IST  daily paper, once a day: at 07:00, or on the first wake-up after it
    16:00-midnight IST  Sunday "this week's papers" roundup, once a week

Neither goes out within an hour of another post (this module's or the news
poster's), so two posts never compete in the feed. Nothing is posted between
midnight and 07:00.

A wake-up that comes early (from 02:00) or inside the one-hour gap doesn't
give up: with --wait it sleeps inside the run (up to MAX_WAIT_MINUTES) and
posts as soon as the slot opens.

    python -m research.schedule          # decide now; sets step output mode=daily|digest|none
    python -m research.schedule --wait   # wait in this run until due (what the workflow uses)

Standard library only, so it runs before anything is installed.
"""

import json
import os
import subprocess
import sys
import time
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
    if today < config.SCHEDULE_START:
        return None, f"automatic posting starts on {config.SCHEDULE_START:%d %B %Y}"

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


def next_due(now, ledger, news_last=None, horizon_minutes=0, step_minutes=5):
    """('daily' | 'digest', when) for the first moment within the horizon a post
    becomes due, assuming nothing else posts meanwhile; (None, None) if none."""
    for k in range(int(horizon_minutes // step_minutes) + 1):
        moment = now + timedelta(minutes=k * step_minutes)
        mode, _ = decide(moment, ledger, news_last)
        if mode:
            return mode, moment
    return None, None


def pull_latest():
    """Fetch posts made while this run waited (the news poster's progress, mostly)."""
    try:
        subprocess.run(["git", "pull", "--quiet", "--rebase"], cwd=config.REPO,
                       capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired):
        pass


def wait_until_due(max_minutes):
    """Sleep inside this run until a post is due, re-checking every 10 minutes.

    GitHub wakes this workflow only a few times a day, so a wake-up that comes
    early (up to 5 h before 07:00) or during the one-hour gap after another post waits
    here and posts, instead of exiting and hoping for another wake-up.
    """
    deadline = datetime.now(config.IST) + timedelta(minutes=max_minutes)
    first = True
    while True:
        if not first:
            pull_latest()
        first = False
        now = datetime.now(config.IST)
        ledger, news_last = state.load_ledger(), news_last_posted()
        mode, reason = decide(now, ledger, news_last)
        if mode:
            return mode, reason
        remaining = (deadline - now).total_seconds() / 60
        upcoming, when = next_due(now, ledger, news_last, remaining)
        if not upcoming:
            return None, reason
        pause = min((when - now).total_seconds() + 30, 600)
        print(f"{reason}. Next chance {when:%H:%M} IST: waiting in this run ({int(pause // 60)} min, then re-checking)", flush=True)
        time.sleep(max(30, pause))


def main():
    if "--wait" in sys.argv[1:]:
        mode, reason = wait_until_due(config.MAX_WAIT_MINUTES)
    else:
        mode, reason = decide(datetime.now(config.IST), state.load_ledger(), news_last_posted())
    print(reason)
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a", encoding="utf-8") as f:
            f.write(f"mode={mode or 'none'}\n")


if __name__ == "__main__":
    main()
