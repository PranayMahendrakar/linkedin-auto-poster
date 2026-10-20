# LinkedIn Auto-Poster

Automated LinkedIn posting powered by GitHub Actions. Posts twice daily at **9 AM** and **6 PM IST**, with manual trigger support. New posts, each with its own thumbnail, are written by Claude three times a day so the queue keeps growing.

## How It Works

1. Posts wait in `linkedin_posts.csv`; `post_progress.json` tracks which one is next
2. **Writers** add posts to the queue:
   - **LinkedIn Writer** (`writer.yml`, this repo) runs Claude Code three times a day. Each run researches current AI news for its slot (models and research, the market and industry, policy and practice), writes one post in Pranay's voice, and designs a thumbnail SVG for it
   - **Daily writer** (in the `pranay-mahendrakar.com` repo) adds one more post a day, alongside the blog post it publishes
3. The **poster** (`post.yml`) publishes the next post on schedule. If `images/<post_number>.svg` exists, it is converted to PNG and attached
4. Writing pauses while 30 posts are waiting (`MAX_BUFFER` in `writer.yml`), so news posts never sit for weeks. If the queue ever runs dry, the poster skips the run instead of re-posting old posts

## Project Structure

```
linkedin-auto-poster/
├── .github/workflows/
│   ├── post.yml                 # Poster: 9 AM + 6 PM IST, or manual
│   └── writer.yml               # Writer: 3 runs a day, or manual (with dry run)
├── writer/
│   ├── LINKEDIN_RUN.md          # Brief Claude follows: voice, rules, thumbnail spec
│   └── post_queue.py            # Builds Claude's context, checks its output, appends + pushes
├── images/                      # Thumbnails, one SVG per post number
├── post_next.py                 # Posts the next item & exits
├── renew_linkedin_token.py      # Gets a new LinkedIn token and saves it to GitHub
├── linkedin_posts.csv           # The queue
├── post_progress.json           # Current position (auto-updated)
└── requirements.txt
```

## Setup

### Secrets (Settings → Secrets and variables → Actions)

| Secret | Used by | How to get it |
|--------|---------|---------------|
| `LINKEDIN_ACCESS_TOKEN` | poster | `python renew_linkedin_token.py` (below) |
| `CLAUDE_CODE_OAUTH_TOKEN` | writer | run `claude setup-token`, then `gh secret set CLAUDE_CODE_OAUTH_TOKEN -R PranayMahendrakar/linkedin-auto-poster` and paste it |

### LinkedIn token (renew every 60 days)

LinkedIn access tokens expire after 60 days. When one does, the poster fails with
"LinkedIn rejected LINKEDIN_ACCESS_TOKEN (401 Unauthorized)". To renew, run this on your PC:

```bash
python renew_linkedin_token.py
```

It asks for your LinkedIn app's client secret, opens the LinkedIn sign-in in your browser, and saves the new token to the GitHub secret. Nothing to copy or paste. It needs Python 3.8+, the GitHub CLI signed in (`gh auth login`), and `http://localhost:8080/callback` listed under the LinkedIn app's **Auth → Authorized redirect URLs**. Add `--no-save` to get and test a token without updating GitHub.

## Manual Runs

**Post now:** Actions → **LinkedIn Auto-Poster** → Run workflow (set how many posts).

**Write a post now:** Actions → **LinkedIn Writer** → Run workflow. Tick **dry run** to see what it would write (the post, thumbnail and Claude's transcript are in the run's artifact) without adding it to the queue.

## Thumbnails

Claude designs each thumbnail as a 1080×1080 SVG following the spec in `writer/LINKEDIN_RUN.md`: navy background, one accent colour, a short bold headline, a simple graphic, and the name in the footer. It renders each one and checks it before saving. `post_queue.py` then rejects anything unsafe or broken (scripts, external links, files over 30 KB, files that don't render). Thumbnails are stored as SVG to keep the repo small and converted to PNG with `rsvg-convert` at posting time, since LinkedIn doesn't accept SVG.

To change the look, edit the **Thumbnail spec** section of `writer/LINKEDIN_RUN.md`. To replace a thumbnail by hand, overwrite `images/<post_number>.svg`.

## Changing Schedules

Edit the cron lines in `.github/workflows/post.yml` (posting) or `writer.yml` (writing). Use [crontab.guru](https://crontab.guru/) to generate cron expressions; times are UTC (IST − 5:30).

## CSV Format

| Column | Required | Description |
|--------|----------|-------------|
| post_number | Yes | Post number (1, 2, 3...); also names the thumbnail file |
| category | Yes | Topic label |
| post_text | Yes | The post content |
| hashtags | No | Hashtags (appended to post) |
