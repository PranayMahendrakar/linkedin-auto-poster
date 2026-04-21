"""
LinkedIn Auto-Poster (text + auto-generated image).

Reads the next unposted row from linkedin_posts.csv, auto-builds a
topic-focused image prompt from the category + post content, generates
the image via Pollinations, uploads it to LinkedIn, and publishes.

If anything image-related fails, falls back to posting text-only so a
post is never skipped.
"""

import os
import csv
import json
import re
import time
from datetime import datetime
from urllib.parse import quote

import requests


# ============ CONFIGURATION ============
ACCESS_TOKEN = os.environ.get("LINKEDIN_ACCESS_TOKEN", "")
CSV_FILE = "linkedin_posts.csv"
PROGRESS_FILE = "post_progress.json"
IMAGE_FILE = "output.png"

# Pollinations config
POLLINATIONS_BASE = "https://image.pollinations.ai/prompt/"
IMAGE_WIDTH = 1024
IMAGE_HEIGHT = 1024
IMAGE_MODEL = "flux"
IMAGE_TIMEOUT = 180

# Locked style — topic-focused, environment-first, NO portraits
STYLE_TEMPLATE = (
    "Wide cinematic establishing shot, environmental scene, concept art. "
    "The scene visually symbolizes the topic: {category}. "
    "Topic context: {context}. "
    "Visualize this through: futuristic technology, tools, workspaces, "
    "architecture, data flows, infrastructure, holographic interfaces, "
    "and symbolic visual metaphors. "
    "Style: modern anime illustration, Makoto Shinkai meets cyberpunk aesthetic, "
    "vibrant cel-shading, highly detailed backgrounds, cinematic lighting. "
    "Color palette: deep navy blue, teal, amber gold, soft neon cyan accents. "
    "The environment is the subject — NOT characters. "
    "No portraits, no close-up faces, no anime-girl aesthetic. "
    "If any figures appear at all, they are tiny distant silhouettes inside a large scene. "
    "1:1 square composition, wide-angle, depth and scale. "
    "No text, no logos, no watermarks."
)


# ============ PROMPT BUILDER ============
def extract_context(post_text: str, max_chars: int = 200) -> str:
    """
    Grab a short visual-hook context from the post text.
    Prefers the first sentence; caps length so the URL stays reasonable.
    """
    if not post_text:
        return ""
    # First sentence — split on . ! ? or newline
    first = re.split(r"[.!?\n]", post_text.strip(), maxsplit=1)[0].strip()
    if not first:
        first = post_text.strip()
    # Trim to max_chars, cut at a word boundary
    if len(first) > max_chars:
        first = first[:max_chars].rsplit(" ", 1)[0] + "..."
    return first


def build_image_prompt(category: str, context: str) -> str:
    """Build a topic-focused Pollinations prompt."""
    return STYLE_TEMPLATE.format(
        category=category or "technology",
        context=context or category or "AI and technology",
    )


# ============ LINKEDIN ============
def get_user_id():
    url = "https://api.linkedin.com/v2/userinfo"
    headers = {"Authorization": f"Bearer {ACCESS_TOKEN}"}
    response = requests.get(url, headers=headers, timeout=30)
    response.raise_for_status()
    return response.json()["sub"]


def register_image_upload(user_id):
    url = "https://api.linkedin.com/v2/assets?action=registerUpload"
    headers = {
        "Authorization": f"Bearer {ACCESS_TOKEN}",
        "Content-Type": "application/json",
    }
    payload = {
        "registerUploadRequest": {
            "recipes": ["urn:li:digitalmediaRecipe:feedshare-image"],
            "owner": f"urn:li:person:{user_id}",
            "serviceRelationships": [
                {
                    "relationshipType": "OWNER",
                    "identifier": "urn:li:userGeneratedContent",
                }
            ],
        }
    }
    response = requests.post(url, headers=headers, json=payload, timeout=30)
    response.raise_for_status()
    data = response.json()
    upload_url = data["value"]["uploadMechanism"][
        "com.linkedin.digitalmedia.uploading.MediaUploadHttpRequest"
    ]["uploadUrl"]
    asset_urn = data["value"]["asset"]
    return upload_url, asset_urn


def upload_image_bytes(upload_url, image_path):
    with open(image_path, "rb") as f:
        image_bytes = f.read()
    headers = {
        "Authorization": f"Bearer {ACCESS_TOKEN}",
        "Content-Type": "application/octet-stream",
    }
    response = requests.put(upload_url, headers=headers, data=image_bytes, timeout=120)
    response.raise_for_status()


def post_to_linkedin(user_id, text, asset_urn=None):
    url = "https://api.linkedin.com/v2/ugcPosts"
    headers = {
        "Authorization": f"Bearer {ACCESS_TOKEN}",
        "Content-Type": "application/json",
        "X-Restli-Protocol-Version": "2.0.0",
    }

    share_content = {
        "shareCommentary": {"text": text},
        "shareMediaCategory": "NONE",
    }

    if asset_urn:
        share_content["shareMediaCategory"] = "IMAGE"
        share_content["media"] = [
            {
                "status": "READY",
                "description": {"text": ""},
                "media": asset_urn,
                "title": {"text": ""},
            }
        ]

    payload = {
        "author": f"urn:li:person:{user_id}",
        "lifecycleState": "PUBLISHED",
        "specificContent": {"com.linkedin.ugc.ShareContent": share_content},
        "visibility": {"com.linkedin.ugc.MemberNetworkVisibility": "PUBLIC"},
    }
    response = requests.post(url, headers=headers, json=payload, timeout=30)
    return response.status_code, response.json()


# ============ IMAGE GENERATION ============
def generate_image(prompt, seed, out_path=IMAGE_FILE):
    url = (
        f"{POLLINATIONS_BASE}{quote(prompt)}"
        f"?width={IMAGE_WIDTH}&height={IMAGE_HEIGHT}"
        f"&model={IMAGE_MODEL}&seed={seed}&nologo=true"
    )
    try:
        response = requests.get(url, timeout=IMAGE_TIMEOUT)
        response.raise_for_status()
        if len(response.content) < 2048:
            print(f"WARNING: Pollinations returned only {len(response.content)} bytes.")
            return False
        with open(out_path, "wb") as f:
            f.write(response.content)
        print(f"Image saved to {out_path} ({len(response.content)} bytes)")
        return True
    except Exception as e:
        print(f"WARNING: image generation failed: {e}")
        return False


# ============ CSV + PROGRESS ============
def load_posts(csv_file):
    """Load posts. Keeps raw post_text separate so we can extract context for prompts."""
    posts = []
    with open(csv_file, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            raw_text = row["post_text"].strip()
            display_text = raw_text
            hashtags = row.get("hashtags", "").strip()
            if hashtags:
                display_text += f"\n\n{hashtags}"
            posts.append(
                {
                    "number": row.get("post_number", ""),
                    "category": row.get("category", "").strip(),
                    "raw_text": raw_text,  # for prompt context
                    "text": display_text,  # for LinkedIn post
                }
            )
    return posts


def load_progress():
    if os.path.exists(PROGRESS_FILE):
        with open(PROGRESS_FILE, "r") as f:
            return json.load(f).get("next_index", 0)
    return 0


def save_progress(index):
    with open(PROGRESS_FILE, "w") as f:
        json.dump(
            {"next_index": index, "last_posted_at": datetime.now().isoformat()},
            f,
            indent=2,
        )


# ============ MAIN ============
def post_next():
    print("=" * 55)
    print("LinkedIn Auto-Poster — Text + Auto Image")
    print("=" * 55)

    if not ACCESS_TOKEN:
        print("ERROR: LINKEDIN_ACCESS_TOKEN not set!")
        exit(1)

    user_id = get_user_id()
    print(f"Authenticated. User: {user_id}")

    posts = load_posts(CSV_FILE)
    total = len(posts)
    print(f"Loaded {total} posts from {CSV_FILE}")

    current_index = load_progress()
    print(f"Current progress: {current_index}/{total}")

    if current_index >= total:
        print("All posts published! Looping back to start...")
        current_index = 0

    post = posts[current_index]
    print(f"\nPosting #{post['number']} — {post['category']}")
    print(f"Preview: {post['text'][:100]}...")

    asset_urn = None
    if post["category"]:
        context = extract_context(post["raw_text"])
        image_prompt = build_image_prompt(post["category"], context)

        try:
            seed = int(post["number"]) if post["number"] else current_index
        except ValueError:
            seed = current_index

        print(f"\nGenerating image for: {post['category']}")
        print(f"Context hook: {context}")
        print(f"Seed: {seed}")

        if generate_image(image_prompt, seed=seed):
            try:
                print("Registering upload with LinkedIn...")
                upload_url, asset_urn = register_image_upload(user_id)
                print("Uploading image bytes...")
                upload_image_bytes(upload_url, IMAGE_FILE)
                print("Waiting 5s for asset to be processed...")
                time.sleep(5)
                print(f"Image ready: {asset_urn}")
            except Exception as e:
                print(f"WARNING: image upload failed, falling back to text-only: {e}")
                asset_urn = None
        else:
            print("Falling back to text-only post.")
    else:
        print("\nNo category. Posting text-only.")

    status, response = post_to_linkedin(user_id, post["text"], asset_urn=asset_urn)

    if status == 201:
        print(f"\nSUCCESS! Post ID: {response.get('id')}")
        current_index += 1
        save_progress(current_index)
        print(f"Progress updated: {current_index}/{total}")
    else:
        print(f"\nFAILED! Status {status}: {response}")
        exit(1)

    if current_index < total:
        next_post = posts[current_index]
        print(f"\nNext up: #{next_post['number']} — {next_post['category']}")
    else:
        print("\nAll posts completed!")
    print("=" * 55)


if __name__ == "__main__":
    post_next()
