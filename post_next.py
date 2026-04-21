"""
LinkedIn Auto-Poster (text + AI-composed image).

For each post: asks Qwen (your HF Space) to generate a concrete visual
scene description from the post content, wraps it in a style template,
sends to Pollinations for image generation, then (optionally) uploads
to LinkedIn.

Fallback chain:
  Qwen down  → use category-only scene (still produces an image)
  Image gen fails → post text-only
  Set POST_TO_LINKEDIN = False → generate only, don't post
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
# ⬇⬇⬇ MASTER TOGGLE ⬇⬇⬇
POST_TO_LINKEDIN = True   # False = safe mode (generate image only, don't post)
# ⬆⬆⬆ MASTER TOGGLE ⬆⬆⬆

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

# Qwen config — your own LLM endpoint via api_url.json service registry
QWEN_URL_REGISTRY = "https://raw.githubusercontent.com/PranayMahendrakar/qwen-runner/main/api_url.json"
QWEN_TIMEOUT_WAKE = 90
QWEN_TIMEOUT_WARM = 45


# ============ STYLE TEMPLATE (aesthetics only — NO semantics) ============
STYLE_WRAPPER = (
    "No text. No words. No letters. No numbers. No labels. No logos. "
    "No watermarks. No typography. No human faces, no portraits, no anime characters. "
    "Isometric 3D conceptual tech illustration, clean modern render. "
    "Scene: {scene}. "
    "Color palette: deep navy blue background, teal, amber gold, soft cyan glows. "
    "Composition: centered hero concept, clean negative space, professional depth, "
    "soft rim lighting, subtle glow, cinematic atmosphere, polished 3D finish. "
    "1:1 square composition, high detail."
)


# ============ QWEN PROMPT ENGINEERING ============
QWEN_SYSTEM = (
    "You are a visual prompt designer for an AI image generator. "
    "Given a LinkedIn post's topic and excerpt, you will:\n"
    "1. First, understand what the topic SPECIFICALLY means (read the excerpt carefully)\n"
    "2. Then write ONE concrete visual scene that represents it\n\n"
    "Rules for the scene:\n"
    "- Use SPECIFIC objects: machines, buildings, devices, geometric shapes, tech hardware\n"
    "- Use symbolic VISUAL metaphor grounded in the actual topic content\n"
    "- NO vague words: 'vibrant', 'innovation', 'efficiency', 'seamless', 'dynamic', "
    "'collective intelligence', 'agnostic' are BANNED\n"
    "- NO human faces, NO text/words in the image, NO abstract adjective-soup\n"
    "- NO style/lighting/color instructions (handled separately)\n"
    "- ONE sentence, 20-40 words, concrete nouns only\n"
    "- Start directly with the scene, no preamble like 'Here is' or 'Scene:'\n\n"
    "Think: if a stranger saw this image, would they guess the topic? If not, be more specific."
)

QWEN_FEWSHOT = [
    {
        "role": "user",
        "content": (
            "Topic: AI in Supply Chain\n"
            "Excerpt: COVID exposed how fragile global supply chains are. AI is rebuilding them "
            "to be resilient with demand forecasting, route optimization, and disruption detection."
        ),
    },
    {
        "role": "assistant",
        "content": (
            "Glowing logistics network of interconnected warehouses, cargo ships and delivery drones "
            "flowing along luminous routes across a stylized world map, with data streams bridging every node."
        ),
    },
    {
        "role": "user",
        "content": (
            "Topic: AI Career Longevity\n"
            "Excerpt: Every 2-3 years the dominant AI technology changes completely. "
            "SVMs to deep learning to transformers to LLMs to agents. Engineers who survive adapt."
        ),
    },
    {
        "role": "assistant",
        "content": (
            "Five glowing geometric pillars rising in timeline sequence — small cube, neural sphere, "
            "transformer stack, large language model core, and a constellation of connected agent nodes "
            "— linked by a light trail."
        ),
    },
    {
        "role": "user",
        "content": (
            "Topic: Agentic Enterprise\n"
            "Excerpt: The enterprise software market is about to be rebuilt around AI agents. "
            "Instead of employees doing repetitive work, autonomous AI agents handle tasks like "
            "emails, scheduling, data entry, and reporting."
        ),
    },
    {
        "role": "assistant",
        "content": (
            "An empty modern office with dozens of translucent robotic agent silhouettes seated at desks, "
            "each handling different tasks — one with floating emails, one with calendars, one with "
            "spreadsheets — connected by glowing data streams."
        ),
    },
]


def get_qwen_url():
    """Fetch current Qwen Space URL from GitHub service registry."""
    try:
        r = requests.get(f"{QWEN_URL_REGISTRY}?t={int(time.time())}", timeout=10)
        r.raise_for_status()
        data = r.json()
        return (data.get("url") or data.get("api_url") or "").rstrip("/")
    except Exception as e:
        print(f"Could not fetch Qwen URL: {e}")
        return None


def clean_scene(raw: str) -> str:
    """Strip preambles, quotes, and markdown from Qwen's response."""
    s = (raw or "").strip()
    s = re.sub(r"^(scene|here is|here's|sure,? here is|visual scene)[\s:\-—]+", "", s, flags=re.IGNORECASE).strip()
    s = s.strip('"\'`').strip()
    s = re.sub(r"\*+", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    s = re.split(r"(?<=[.!])\s", s, maxsplit=1)[0]
    return s


def ask_qwen_for_scene(qwen_url: str, category: str, excerpt: str) -> str:
    """Call Qwen to generate a concrete visual scene for the topic. Returns '' on failure."""
    if not qwen_url:
        return ""

    messages = [{"role": "system", "content": QWEN_SYSTEM}]
    messages.extend(QWEN_FEWSHOT)
    messages.append({
        "role": "user",
        "content": f"Topic: {category}\nExcerpt: {excerpt[:800]}",
    })

    try:
        r = requests.post(
            f"{qwen_url}/chat",
            json={"messages": messages},
            timeout=QWEN_TIMEOUT_WAKE,
        )
        r.raise_for_status()
        data = r.json()
        scene = clean_scene(data.get("response", ""))
        if len(scene) < 10 or len(scene) > 500:
            print(f"Qwen returned unusable scene (len={len(scene)}): {scene!r}")
            return ""
        return scene
    except Exception as e:
        print(f"Qwen request failed: {e}")
        return ""


# ============ PROMPT BUILDER ============
def extract_excerpt(post_text: str, max_chars: int = 800) -> str:
    """Get enough of the post for Qwen to understand the topic (first 2 paragraphs)."""
    if not post_text:
        return ""
    paragraphs = post_text.strip().split("\n\n")
    chunk = "\n\n".join(paragraphs[:2])
    if len(chunk) > max_chars:
        chunk = chunk[:max_chars].rsplit(" ", 1)[0] + "..."
    return chunk


def build_image_prompt(scene: str) -> str:
    return STYLE_WRAPPER.format(scene=scene)


def fallback_scene(category: str) -> str:
    """Used when Qwen is unavailable — generic but on-topic scene."""
    return (
        f"symbolic conceptual tech visualization of '{category}', "
        f"abstract geometric shapes, floating tech objects, circuit patterns, data flows"
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

    share_content = {"shareCommentary": {"text": text}, "shareMediaCategory": "NONE"}

    if asset_urn:
        share_content["shareMediaCategory"] = "IMAGE"
        share_content["media"] = [{
            "status": "READY",
            "description": {"text": ""},
            "media": asset_urn,
            "title": {"text": ""},
        }]

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
    posts = []
    with open(csv_file, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            raw_text = row["post_text"].strip()
            display_text = raw_text
            hashtags = row.get("hashtags", "").strip()
            if hashtags:
                display_text += f"\n\n{hashtags}"
            posts.append({
                "number": row.get("post_number", ""),
                "category": row.get("category", "").strip(),
                "raw_text": raw_text,
                "text": display_text,
            })
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
            f, indent=2,
        )


# ============ MAIN ============
def post_next():
    print("=" * 55)
    print("LinkedIn Auto-Poster — Qwen Scene + Flux Image")
    print(f"POST_TO_LINKEDIN = {POST_TO_LINKEDIN}")
    if not POST_TO_LINKEDIN:
        print("*** SAFE MODE: image will be generated but NOT posted ***")
    print("=" * 55)

    posts = load_posts(CSV_FILE)
    total = len(posts)
    print(f"Loaded {total} posts from {CSV_FILE}")

    current_index = load_progress()
    print(f"Current progress: {current_index}/{total}")

    if current_index >= total:
        print("All posts published! Looping back to start...")
        current_index = 0

    post = posts[current_index]
    print(f"\nPreviewing #{post['number']} — {post['category']}")
    print(f"Post text preview: {post['text'][:100]}...")

    # ---- Step 1: Ask Qwen for a scene (with fallback) ----
    scene = ""
    if post["category"]:
        excerpt = extract_excerpt(post["raw_text"])
        print("\n[Qwen] Fetching URL from registry...")
        qwen_url = get_qwen_url()
        if qwen_url:
            print(f"[Qwen] URL: {qwen_url}")
            print("[Qwen] Requesting scene description...")
            scene = ask_qwen_for_scene(qwen_url, post["category"], excerpt)
            if scene:
                print(f"[Qwen] Scene: {scene}")
            else:
                print("[Qwen] No usable response. Using fallback scene.")
        else:
            print("[Qwen] Offline. Using fallback scene.")

        if not scene:
            scene = fallback_scene(post["category"])
            print(f"[Fallback] Scene: {scene}")

        # ---- Step 2: Build final image prompt and generate ----
        image_prompt = build_image_prompt(scene)
        try:
            seed = int(post["number"]) if post["number"] else current_index
        except ValueError:
            seed = current_index

        print(f"\n[Flux] Generating image (seed={seed}, prompt len={len(image_prompt)})")
        image_ok = generate_image(image_prompt, seed=seed)

        # ---- SAFE MODE exit ----
        if not POST_TO_LINKEDIN:
            if image_ok:
                print(f"\n*** SAFE MODE complete. Image saved to {IMAGE_FILE} ***")
                print(f"*** Scene used: {scene}")
                print("*** No LinkedIn post was made. No progress was updated. ***")
            else:
                print("\n*** SAFE MODE: image generation failed. ***")
            return

        # ---- Step 3: Upload to LinkedIn ----
        asset_urn = None
        if image_ok:
            try:
                if not ACCESS_TOKEN:
                    print("ERROR: LINKEDIN_ACCESS_TOKEN not set!")
                    exit(1)
                user_id = get_user_id()
                print(f"\n[LinkedIn] Authenticated. User: {user_id}")
                print("[LinkedIn] Registering upload...")
                upload_url, asset_urn = register_image_upload(user_id)
                print("[LinkedIn] Uploading image bytes...")
                upload_image_bytes(upload_url, IMAGE_FILE)
                print("[LinkedIn] Waiting 5s for asset processing...")
                time.sleep(5)
                print(f"[LinkedIn] Asset ready: {asset_urn}")
            except Exception as e:
                print(f"WARNING: image upload failed, falling back to text-only: {e}")
                asset_urn = None
        else:
            print("Image gen failed. Falling back to text-only post.")
    else:
        print("\nNo category. Skipping image.")
        if not POST_TO_LINKEDIN:
            print("SAFE MODE: exiting without posting.")
            return
        asset_urn = None

    # ---- Step 4: Publish ----
    if not ACCESS_TOKEN:
        print("ERROR: LINKEDIN_ACCESS_TOKEN not set!")
        exit(1)
    try:
        user_id
    except NameError:
        user_id = get_user_id()
        print(f"[LinkedIn] Authenticated. User: {user_id}")

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
