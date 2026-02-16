import requests
import csv
import os
import json
from datetime import datetime


# ============ CONFIGURATION ============
ACCESS_TOKEN = os.environ.get("LINKEDIN_ACCESS_TOKEN", "")
CSV_FILE = "linkedin_posts.csv"
PROGRESS_FILE = "post_progress.json"


def get_user_id():
    """Get LinkedIn user ID from access token."""
    url = "https://api.linkedin.com/v2/userinfo"
    headers = {"Authorization": f"Bearer {ACCESS_TOKEN}"}
    response = requests.get(url, headers=headers)
    response.raise_for_status()
    return response.json()["sub"]


def post_to_linkedin(user_id, text):
    """Post a single text post to LinkedIn."""
    url = "https://api.linkedin.com/v2/ugcPosts"
    headers = {
        "Authorization": f"Bearer {ACCESS_TOKEN}",
        "Content-Type": "application/json",
        "X-Restli-Protocol-Version": "2.0.0",
    }
    payload = {
        "author": f"urn:li:person:{user_id}",
        "lifecycleState": "PUBLISHED",
        "specificContent": {
            "com.linkedin.ugc.ShareContent": {
                "shareCommentary": {"text": text},
                "shareMediaCategory": "NONE",
            }
        },
        "visibility": {"com.linkedin.ugc.MemberNetworkVisibility": "PUBLIC"},
    }
    response = requests.post(url, headers=headers, json=payload)
    return response.status_code, response.json()


def load_posts(csv_file):
    """Load all posts from CSV file."""
    posts = []
    with open(csv_file, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            text = row["post_text"].strip()
            hashtags = row.get("hashtags", "").strip()
            if hashtags:
                text += f"\n\n{hashtags}"
            posts.append({
                "number": row.get("post_number", ""),
                "category": row.get("category", ""),
                "text": text,
            })
    return posts


def load_progress():
    """Load current post index from progress file."""
    if os.path.exists(PROGRESS_FILE):
        with open(PROGRESS_FILE, "r") as f:
            data = json.load(f)
            return data.get("next_index", 0)
    return 0


def save_progress(index):
    """Save current post index to progress file."""
    data = {
        "next_index": index,
        "last_posted_at": datetime.now().isoformat(),
    }
    with open(PROGRESS_FILE, "w") as f:
        json.dump(data, f, indent=2)


def post_next():
    """Post the next unposted item from CSV and update progress."""
    print("=" * 55)
    print("LinkedIn Auto-Poster — GitHub Actions Edition")
    print("=" * 55)

    if not ACCESS_TOKEN:
        print("ERROR: LINKEDIN_ACCESS_TOKEN not set!")
        exit(1)

    # Authenticate
    user_id = get_user_id()
    print(f"Authenticated! User: {user_id}")

    # Load posts
    posts = load_posts(CSV_FILE)
    total = len(posts)
    print(f"Loaded {total} posts from {CSV_FILE}")

    # Load progress
    current_index = load_progress()
    print(f"Current progress: {current_index}/{total}")

    if current_index >= total:
        print("All posts have been published! Resetting to start...")
        current_index = 0

    # Get the next post
    post = posts[current_index]
    print(f"\nPosting #{post['number']} — {post['category']}")
    print(f"Preview: {post['text'][:100]}...")

    # Post it
    status, response = post_to_linkedin(user_id, post["text"])

    if status == 201:
        print(f"SUCCESS! Posted! ID: {response.get('id')}")
        current_index += 1
        save_progress(current_index)
        print(f"Progress updated: {current_index}/{total}")
    else:
        print(f"FAILED! Status {status}: {response}")
        exit(1)

    if current_index < total:
        print(f"\nNext post will be #{current_index + 1}")
    else:
        print("\nAll posts completed!")
    print("=" * 55)


if __name__ == "__main__":
    post_next()
