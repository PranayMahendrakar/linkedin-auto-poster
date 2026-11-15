"""
Settings for the research-paper poster. Everything tunable lives here.

The research module is separate from the news poster (post_next.py): it has its
own queue, its own progress file and its own workflow, and only shares the
LINKEDIN_ACCESS_TOKEN secret.
"""

import os
from datetime import date, time, timedelta, timezone

# ---- Whose papers -----------------------------------------------------------
# Papers are picked by ORCID, never by name: a name search on Zenodo also
# returns other people called Mahendrakar, and Life of Research hosts papers by
# other authors too.
ORCID = "0009-0003-7224-029X"
AUTHOR_NAME = "Pranay Mahendrakar"

# ---- Sources ----------------------------------------------------------------
ZENODO_API = "https://zenodo.org/api/records"
LOR_BASE = "https://lifeofresearch.com"
# Zenodo answers browser-like user agents with 403; a plain client name works.
USER_AGENT = "research-linkedin-poster/1.0"
HTTP_TIMEOUT = 60

# ---- Schedule (IST) ---------------------------------------------------------
IST = timezone(timedelta(hours=5, minutes=30), "IST")
SCHEDULE_START = date(2026, 11, 16)   # automatic posts begin this day; manual runs ignore it
DAILY_SLOT = time(12, 0)          # one paper a day, any time from 12:00 ...
DAILY_LAST_CALL = time(17, 30)    # ... until 17:30; after that the day is skipped
DIGEST_WEEKDAY = 6                # Sunday (Monday is 0)
DIGEST_SLOT = time(16, 0)
DIGEST_LAST_CALL = time(17, 45)   # the news poster's 18:00 post comes next
MIN_GAP_MINUTES = 60              # never post within an hour of another post

# ---- LinkedIn -----------------------------------------------------------------
LINKEDIN_API = "https://api.linkedin.com"
# Versions are supported for at least a year; bump this before it is sunset.
LINKEDIN_VERSION = "202610"
COMMENTARY_LIMIT = 3000           # LinkedIn's hard limit
COMMENTARY_BUDGET = 2900          # what we allow ourselves
DOC_MAX_BYTES = 100 * 1024 * 1024
DOC_MAX_PAGES = 300

# ---- Hashtags -----------------------------------------------------------------
# Every research post ends with these, after the paper's own topic tags.
FIXED_HASHTAGS = ["#Research", "#ResearchPaper", "#OpenScience", "#LifeOfResearch"]
SIGNATURE_HASHTAG = FIXED_HASHTAGS[-1]
MAX_TOPIC_HASHTAGS = 4            # topic tags per post, before the fixed ones (8 in all)

# ---- Files ------------------------------------------------------------------
HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
CATALOG_FILE = os.path.join(HERE, "catalog.json")
LEDGER_FILE = os.path.join(HERE, "posted.json")
OVERRIDES_FILE = os.path.join(HERE, "overrides.json")
DRAFTS_DIR = os.path.join(HERE, "drafts")
OUT_DIR = os.path.join(HERE, "out")
NEWS_PROGRESS_FILE = os.path.join(REPO, "post_progress.json")

# ---- Writer -----------------------------------------------------------------
WRITER_BUFFER = 7                 # keep a week of posts written ahead
WRITER_MAX_PER_RUN = 3
PAPER_TEXT_CHARS = 60000          # how much of each paper Claude gets to read
