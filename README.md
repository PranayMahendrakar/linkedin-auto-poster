# LinkedIn Auto-Poster

Automated LinkedIn posting powered by GitHub Actions. Posts twice daily at **9 AM** and **6 PM IST**, with manual trigger support.

## How It Works

1. Your posts are stored in `linkedin_posts.csv`
2. `post_progress.json` tracks which post is next
3. GitHub Actions runs the script on schedule (or manually)
4. The script posts the next unposted item and commits the updated progress
5. Repeats automatically — hands-free!

## Project Structure

```
linkedin-auto-poster/
├── .github/workflows/post.yml   # GitHub Actions workflow (cron + manual)
├── post_next.py                  # Main script — posts next item & exits
├── linkedin_posts.csv            # Your 100 posts (CSV format)
├── post_progress.json            # Tracks current position (auto-updated)
├── requirements.txt              # Python dependencies
└── README.md
```

## Setup Guide

### Step 1: Get LinkedIn Access Token

1. Go to [LinkedIn Developer Portal](https://www.linkedin.com/developers/)
2. Create an app and request `w_member_social` and `openid` scopes
3. Generate an access token via OAuth 2.0
4. Copy the access token

### Step 2: Add Secret to GitHub

1. Go to your repo **Settings** → **Secrets and variables** → **Actions**
2. Click **New repository secret**
3. Name: `LINKEDIN_ACCESS_TOKEN`
4. Value: paste your LinkedIn access token
5. Click **Add secret**

### Step 3: Add Your Posts to CSV

Edit `linkedin_posts.csv` with your posts. Format:

```csv
post_number,category,post_text,hashtags
1,AI Basics,"Your post text here",#AI #ML #Tech
2,Career Tips,"Another post text",#Career #DataScience
```

### Step 4: Done!

The workflow will automatically run at:
- **9:00 AM IST** (3:30 AM UTC)
- **6:00 PM IST** (12:30 PM UTC)

## Manual Posting

To post immediately:

1. Go to **Actions** tab in your repo
2. Click **LinkedIn Auto-Poster** workflow
3. Click **Run workflow**
4. Set number of posts (default: 1)
5. Click **Run workflow** button

## Changing Schedule

Edit `.github/workflows/post.yml` and update the cron expressions:

```yaml
schedule:
  - cron: '30 3 * * *'   # 9:00 AM IST
  - cron: '30 12 * * *'  # 6:00 PM IST
```

Use [crontab.guru](https://crontab.guru/) to generate cron expressions.

## Resetting Progress

To start over from post #1, edit `post_progress.json`:

```json
{
  "next_index": 0,
  "last_posted_at": null
}
```

## CSV Format

| Column | Required | Description |
|--------|----------|-------------|
| post_number | Yes | Post number (1, 2, 3...) |
| category | Yes | Category name |
| post_text | Yes | The post content |
| hashtags | No | Hashtags (appended to post) |
