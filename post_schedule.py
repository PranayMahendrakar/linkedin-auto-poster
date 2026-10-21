"""
Decide whether the poster should post now, by the clock in India.

GitHub starts scheduled runs late (by hours on busy days) and sometimes skips
them, so post.yml wakes up every 20 minutes and asks this script. Slots:

    09:00 IST  morning post, sent any time from 09:00 until 18:00
    18:00 IST  evening post, sent any time from 18:00 until midnight

A slot is due until something has been posted since it started, so a late
run still posts and a slot never gets two posts. Nothing is posted between
midnight and 09:00 IST: a missed evening post is skipped, not sent at 1 AM.

    python post_schedule.py    # prints the decision; sets the step output due=true/false

Standard library only, so it runs before anything is installed.
"""

import json
import os
from datetime import datetime, time, timedelta, timezone

IST = timezone(timedelta(hours=5, minutes=30), "IST")
SLOTS = [time(9, 0), time(18, 0)]
PROGRESS_FILE = "post_progress.json"


def parse_time(value):
    """last_posted_at as an aware datetime, or None.

    Entries written before this script existed have no offset; they came from
    datetime.now() on a GitHub runner, which runs in UTC.
    """
    if not value:
        return None
    try:
        moment = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment


def current_slot(now):
    """Start of the slot `now` falls in, or None before the first slot of the day."""
    now = now.astimezone(IST)
    started = [slot for slot in SLOTS if now.time() >= slot]
    if not started:
        return None
    return datetime.combine(now.date(), started[-1], tzinfo=IST)


def is_due(now, last_posted):
    """(due, reason) for a run at `now` when the last post went out at `last_posted`."""
    slot = current_slot(now)
    if slot is None:
        return False, f"before the first slot of the day ({SLOTS[0]:%H:%M} IST)"
    if last_posted is not None and last_posted >= slot:
        sent = last_posted.astimezone(IST)
        return False, f"the {slot:%H:%M} IST post already went out at {sent:%H:%M} IST"
    return True, f"the {slot:%H:%M} IST post is due"


def main():
    last_posted = None
    if os.path.exists(PROGRESS_FILE):
        with open(PROGRESS_FILE, encoding="utf-8") as f:
            last_posted = parse_time(json.load(f).get("last_posted_at"))

    now = datetime.now(timezone.utc)
    due, reason = is_due(now, last_posted)
    print(f"{now.astimezone(IST):%Y-%m-%d %H:%M} IST: {'posting' if due else 'nothing to do'} ({reason}).")

    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        with open(output, "a", encoding="utf-8") as f:
            f.write(f"due={'true' if due else 'false'}\n")


if __name__ == "__main__":
    main()
