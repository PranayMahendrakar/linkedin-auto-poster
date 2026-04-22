"""
LinkedIn Auto-Poster (text + AI-composed image with style rotation).

Scene chain: Pollinations text → Qwen runner → content-aware fallback.
Style rotation: 8 distinct visual styles, selected deterministically by post number.
Each post always picks the same style on retry, but styles rotate across posts.

Set POST_TO_LINKEDIN = False for dry-run (image only, no posting).
"""

import os
import csv
import json
import re
import time
import random
from datetime import datetime
from urllib.parse import quote

import requests


# ============ CONFIGURATION ============
# ⬇⬇⬇ MASTER TOGGLE ⬇⬇⬇
POST_TO_LINKEDIN = True
# ⬆⬆⬆ MASTER TOGGLE ⬆⬆⬆

ACCESS_TOKEN = os.environ.get("LINKEDIN_ACCESS_TOKEN", "")
CSV_FILE = "linkedin_posts.csv"
PROGRESS_FILE = "post_progress.json"
IMAGE_FILE = "output.png"

POLLINATIONS_IMAGE = "https://image.pollinations.ai/prompt/"
IMAGE_WIDTH = 1024
IMAGE_HEIGHT = 1024
IMAGE_MODEL = "flux"
IMAGE_TIMEOUT = 180

POLLINATIONS_TEXT = "https://text.pollinations.ai/openai"
TEXT_MODEL = "openai"
TEXT_TIMEOUT = 45

QWEN_URL_REGISTRY = "https://raw.githubusercontent.com/PranayMahendrakar/qwen-runner/main/api_url.json"
QWEN_TIMEOUT_WAKE = 90


# ============ STYLE LIBRARY ============
# Each style = (style_hint for LLM scene, full wrapper for image prompt)
# "Universal negatives" stay the same across all styles: no text, no faces.
UNIVERSAL_NEGATIVES = (
    "No text, no words, no letters, no numbers, no labels, no logos, no watermarks, "
    "no typography, no human faces, no portraits, no close-up people."
)

STYLES = {
    "flat_editorial": {
        "scene_hint": "think editorial magazine illustration with simple iconic shapes",
        "wrapper": (
            "Bold flat vector editorial illustration, New Yorker meets tech magazine cover style. "
            "Simple geometric shapes, limited color palette, confident composition. "
            "Scene: {scene}. "
            "Colors: muted navy, warm orange, cream, soft teal accents. "
            "Minimalist, sophisticated, hand-crafted feel, slight texture. "
            f"{UNIVERSAL_NEGATIVES} "
            "1:1 square composition."
        ),
    },
    "synthwave": {
        "scene_hint": "think 80s arcade, neon chrome retro-futurism",
        "wrapper": (
            "Retro synthwave illustration, 1980s Miami aesthetic, Blade Runner neon, vaporwave poster. "
            "Grid floor, chrome reflections, sunset gradients, glowing neon outlines. "
            "Scene: {scene}. "
            "Colors: hot pink, electric cyan, deep purple, golden yellow horizon. "
            "High contrast, dreamy atmosphere, film grain, nostalgic energy. "
            f"{UNIVERSAL_NEGATIVES} "
            "1:1 square composition."
        ),
    },
    "cyberpunk_photo": {
        "scene_hint": "think gritty near-future city at night, cinematic realism",
        "wrapper": (
            "Cinematic cyberpunk photorealistic scene, Blade Runner 2049 aesthetic. "
            "Wet streets, neon signs, rain, volumetric fog, dramatic atmospheric lighting. "
            "Scene: {scene}. "
            "Colors: deep teal shadows, magenta and amber neon highlights, cool blue midtones. "
            "Photographic depth of field, shallow focus, cinematic anamorphic lens. "
            f"{UNIVERSAL_NEGATIVES} "
            "1:1 square composition."
        ),
    },
    "blueprint": {
        "scene_hint": "think engineering schematic, annotated with simple line callouts (no readable text)",
        "wrapper": (
            "Technical blueprint schematic illustration, engineering drawing aesthetic. "
            "White and cyan line art on deep navy blue paper, drafting grid, "
            "measurement markers, construction lines, exploded view. "
            "Scene: {scene}. "
            "Colors: navy blue background, crisp white and cyan lines, occasional amber highlight. "
            "Precise, clean, designed-by-an-engineer feel. "
            f"{UNIVERSAL_NEGATIVES} No readable text or numbers on the blueprint. "
            "1:1 square composition."
        ),
    },
    "paper_collage": {
        "scene_hint": "think physical cut-paper collage, torn magazine scraps",
        "wrapper": (
            "Mixed-media paper collage illustration, cut-paper and torn-edge style. "
            "Layered paper textures, visible ripped edges, hand-drawn pencil strokes, "
            "halftone dot patterns, subtle shadows suggesting physical depth. "
            "Scene: {scene}. "
            "Colors: warm cream background, navy blue, muted orange, soft teal, occasional gold leaf. "
            "Tactile, human, imperfect, handmade quality. "
            f"{UNIVERSAL_NEGATIVES} "
            "1:1 square composition."
        ),
    },
    "oil_painting": {
        "scene_hint": "think expressive oil painting with emotional mood",
        "wrapper": (
            "Expressive oil painting, thick impasto brushstrokes, impressionist technique. "
            "Visible canvas texture, bold painterly marks, dramatic light and shadow. "
            "Scene: {scene}. "
            "Colors: deep navy and teal shadows, warm amber and ochre highlights, "
            "rich dark tones, atmospheric mood. "
            "Fine art gallery quality, emotional weight, museum-piece aesthetic. "
            f"{UNIVERSAL_NEGATIVES} "
            "1:1 square composition."
        ),
    },
    "dark_minimalist": {
        "scene_hint": "think Apple product render, single hero object, lots of empty space",
        "wrapper": (
            "Premium dark minimalist product render, Apple keynote aesthetic. "
            "Single hero subject floating in deep black void, dramatic studio lighting, "
            "sharp specular highlights, soft shadows, polished surfaces. "
            "Scene: {scene}. "
            "Colors: pure black background, subtle teal rim light, amber gold accent glow, white highlights. "
            "Expensive, refined, designed-with-intention feel. Massive negative space. "
            f"{UNIVERSAL_NEGATIVES} "
            "1:1 square composition."
        ),
    },
    "low_poly": {
        "scene_hint": "think Monument Valley game, faceted geometric forms",
        "wrapper": (
            "Low-poly 3D illustration, Monument Valley video game aesthetic. "
            "Faceted triangular geometry, flat-shaded surfaces, playful isometric perspective, "
            "clean edges, stylized simplified forms. "
            "Scene: {scene}. "
            "Colors: soft pastel palette with navy, teal, coral, cream, and golden yellow. "
            "Charming, dreamlike, slightly whimsical, modern indie game art. "
            f"{UNIVERSAL_NEGATIVES} "
            "1:1 square composition."
        ),
    },
}

STYLE_KEYS = list(STYLES.keys())


def pick_style(seed: int) -> str:
    """Deterministically pick a style based on post number."""
    rng = random.Random(seed)
    return rng.choice(STYLE_KEYS)


def build_image_prompt(scene: str, style_key: str) -> str:
    return STYLES[style_key]["wrapper"].format(scene=scene)


# ============ LLM SCENE PROMPTING ============
def build_scene_system(style_hint: str) -> str:
    return (
        "You are a visual prompt designer for an AI image generator. "
        "Given a LinkedIn post's topic and excerpt, you will:\n"
        "1. First, understand what the topic SPECIFICALLY means (read the excerpt carefully)\n"
        "2. Then write ONE concrete visual scene that represents it\n\n"
        f"Style guidance: {style_hint}. "
        "But do NOT include style/lighting/color words — describe only the subject matter.\n\n"
        "Rules for the scene:\n"
        "- Use SPECIFIC objects: machines, buildings, devices, geometric shapes, tech hardware\n"
        "- Use symbolic VISUAL metaphor grounded in the actual topic content\n"
        "- NO vague words: 'vibrant', 'innovation', 'efficiency', 'seamless', 'dynamic' are BANNED\n"
        "- NO human faces, NO text/words in the image\n"
        "- ONE sentence, 20-40 words, concrete nouns only\n"
        "- Start directly with the scene, no preamble\n\n"
        "Think: if a stranger saw this image, would they guess the topic? If not, be more specific."
    )


SCENE_FEWSHOT = [
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
            "Interconnected warehouses, cargo ships, and delivery drones flowing along routes "
            "across a stylized world map, with data streams bridging every node."
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
            "Five pillars rising in timeline sequence — small cube, neural sphere, transformer stack, "
            "large language model core, and a constellation of connected agent nodes, linked by a path."
        ),
    },
    {
        "role": "user",
        "content": (
            "Topic: Agentic Enterprise\n"
            "Excerpt: The enterprise software market is about to be rebuilt around AI agents. "
            "Instead of employees doing repetitive work, autonomous AI agents handle tasks."
        ),
    },
    {
        "role": "assistant",
        "content": (
            "An empty modern office with dozens of robotic agent silhouettes at desks, "
            "each handling different tasks — emails, calendars, spreadsheets — connected by data streams."
        ),
    },
]


def build_llm_messages(category: str, excerpt: str, style_hint: str) -> list:
    msgs = [{"role": "system", "content": build_scene_system(style_hint)}]
    msgs.extend(SCENE_FEWSHOT)
    msgs.append({
        "role": "user",
        "content": f"Topic: {category}\nExcerpt: {excerpt[:800]}",
    })
    return msgs


def clean_scene(raw: str) -> str:
    s = (raw or "").strip()
    s = re.sub(r"^(scene|here is|here's|sure,? here is|visual scene)[\s:\-—]+", "", s, flags=re.IGNORECASE).strip()
    s = s.strip('"\'`').strip()
    s = re.sub(r"\*+", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    s = re.split(r"(?<=[.!])\s", s, maxsplit=1)[0]
    return s


# ============ PROVIDER 1: POLLINATIONS TEXT ============
def ask_pollinations_for_scene(category: str, excerpt: str, style_hint: str) -> str:
    messages = build_llm_messages(category, excerpt, style_hint)
    try:
        r = requests.post(
            POLLINATIONS_TEXT,
            json={"model": TEXT_MODEL, "messages": messages, "temperature": 0.7, "max_tokens": 120},
            timeout=TEXT_TIMEOUT,
        )
        r.raise_for_status()
        data = r.json()
        content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
        scene = clean_scene(content)
        if 10 <= len(scene) <= 500:
            return scene
        print(f"Pollinations text returned unusable scene: {scene!r}")
        return ""
    except Exception as e:
        print(f"Pollinations text request failed: {e}")
        return ""


# ============ PROVIDER 2: QWEN RUNNER ============
def get_qwen_url():
    try:
        r = requests.get(f"{QWEN_URL_REGISTRY}?t={int(time.time())}", timeout=10)
        r.raise_for_status()
        data = r.json()
        return (data.get("url") or data.get("api_url") or "").rstrip("/")
    except Exception as e:
        print(f"Could not fetch Qwen URL: {e}")
        return None


def qwen_health_check(qwen_url: str) -> bool:
    try:
        r = requests.get(f"{qwen_url}/", timeout=8)
        return r.status_code == 200
    except Exception:
        return False


def ask_qwen_for_scene(qwen_url: str, category: str, excerpt: str, style_hint: str) -> str:
    if not qwen_url or not qwen_health_check(qwen_url):
        print("[Qwen] Space is unreachable.")
        return ""

    messages = build_llm_messages(category, excerpt, style_hint)
    try:
        r = requests.post(f"{qwen_url}/chat", json={"messages": messages}, timeout=QWEN_TIMEOUT_WAKE)
        r.raise_for_status()
        data = r.json()
        scene = clean_scene(data.get("response", ""))
        if 10 <= len(scene) <= 500:
            return scene
        print(f"Qwen returned unusable scene (len={len(scene)}): {scene!r}")
        return ""
    except Exception as e:
        print(f"Qwen request failed: {e}")
        return ""


# ============ PROVIDER 3: CONTENT-AWARE FALLBACK ============
_STOPWORDS = {
    "the","this","that","these","those","and","but","for","with","from",
    "into","onto","your","you","we","our","they","their","them","a","an",
    "is","are","was","were","be","been","being","have","has","had",
    "will","would","should","could","can","may","might","must",
    "on","in","at","of","to","as","by","or","if","it","its",
    "about","over","under","more","most","less","least","just","only",
    "also","too","very","really","actually","even","still","now","then",
    "here","there","when","where","which","what","while","why","how",
    "2024","2025","2026","2027","2028","2029","2030",
    "biggest","smart","modern","hidden","backbone","invested","companies",
}


def content_aware_fallback(category: str, post_text: str) -> str:
    text = post_text[:2000]
    sentences = re.split(r"[.!?\n]+", text)
    mid_sentence_text = " ".join(
        " ".join(s.strip().split()[1:]) for s in sentences if len(s.strip().split()) > 1
    )
    multi_caps = re.findall(r"\b[A-Z][a-z]+(?:\s[A-Z][a-z]+)+\b", mid_sentence_text)
    single_caps = re.findall(r"\b[A-Z][A-Za-z0-9]{2,}\b", mid_sentence_text)
    amounts = re.findall(r"\$?\d+[KMB]\+?|\d+%", text)
    lower_words = re.findall(r"\b[a-z]{5,}\b", text.lower())
    lower_words = [w for w in lower_words if w not in _STOPWORDS]

    seen = set()
    def dedupe(items):
        out = []
        for x in items:
            key = x.lower()
            if key not in seen and key not in _STOPWORDS:
                seen.add(key)
                out.append(x)
        return out

    picks = dedupe(multi_caps)[:3] + dedupe(single_caps)[:4] + dedupe(amounts)[:2] + dedupe(lower_words)[:4]
    keyword_phrase = ", ".join(picks[:8]) if picks else ""
    if keyword_phrase:
        return (
            f"Conceptual visualization representing '{category}' — "
            f"depicting key elements: {keyword_phrase}. "
            f"Abstract shapes, tech objects, data flows, connecting lines."
        )
    return f"Symbolic visualization of '{category}', abstract shapes, tech objects, data flows."


def resolve_scene(category: str, excerpt: str, post_text: str, style_hint: str) -> tuple:
    print("\n[Scene] Trying Pollinations text API...")
    scene = ask_pollinations_for_scene(category, excerpt, style_hint)
    if scene:
        return scene, "pollinations_text"

    print("[Scene] Pollinations text unavailable. Trying Qwen runner...")
    qwen_url = get_qwen_url()
    if qwen_url:
        print(f"[Scene] Qwen URL: {qwen_url}")
        scene = ask_qwen_for_scene(qwen_url, category, excerpt, style_hint)
        if scene:
            return scene, "qwen"

    print("[Scene] Both LLMs unavailable. Using content-aware fallback.")
    return content_aware_fallback(category, post_text), "content_fallback"


# ============ HELPERS ============
def extract_excerpt(post_text: str, max_chars: int = 800) -> str:
    if not post_text:
        return ""
    paragraphs = post_text.strip().split("\n\n")
    chunk = "\n\n".join(paragraphs[:2])
    if len(chunk) > max_chars:
        chunk = chunk[:max_chars].rsplit(" ", 1)[0] + "..."
    return chunk


# ============ LINKEDIN ============
def get_user_id():
    url = "https://api.linkedin.com/v2/userinfo"
    headers = {"Authorization": f"Bearer {ACCESS_TOKEN}"}
    response = requests.get(url, headers=headers, timeout=30)
    response.raise_for_status()
    return response.json()["sub"]


def register_image_upload(user_id):
    url = "https://api.linkedin.com/v2/assets?action=registerUpload"
    headers = {"Authorization": f"Bearer {ACCESS_TOKEN}", "Content-Type": "application/json"}
    payload = {
        "registerUploadRequest": {
            "recipes": ["urn:li:digitalmediaRecipe:feedshare-image"],
            "owner": f"urn:li:person:{user_id}",
            "serviceRelationships": [{
                "relationshipType": "OWNER",
                "identifier": "urn:li:userGeneratedContent",
            }],
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
        f"{POLLINATIONS_IMAGE}{quote(prompt)}"
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
    print("LinkedIn Auto-Poster — Multi-Style Edition")
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

    if not post["category"]:
        print("\nNo category. Skipping image.")
        if not POST_TO_LINKEDIN:
            print("SAFE MODE: exiting without posting.")
            return
        asset_urn = None
    else:
        try:
            seed = int(post["number"]) if post["number"] else current_index
        except ValueError:
            seed = current_index

        # Pick style deterministically based on post number
        style_key = pick_style(seed)
        style_hint = STYLES[style_key]["scene_hint"]
        print(f"\n[Style] Selected: {style_key}")

        excerpt = extract_excerpt(post["raw_text"])
        scene, scene_source = resolve_scene(post["category"], excerpt, post["raw_text"], style_hint)
        print(f"[Scene] Source: {scene_source}")
        print(f"[Scene] {scene}")

        image_prompt = build_image_prompt(scene, style_key)
        print(f"\n[Flux] Generating image (seed={seed}, style={style_key}, prompt len={len(image_prompt)})")
        image_ok = generate_image(image_prompt, seed=seed)

        if not POST_TO_LINKEDIN:
            if image_ok:
                print(f"\n*** SAFE MODE complete. Image saved to {IMAGE_FILE} ***")
                print(f"*** Style:  {style_key}")
                print(f"*** Source: {scene_source}")
                print(f"*** Scene:  {scene}")
                print("*** No LinkedIn post was made. No progress was updated. ***")
            else:
                print("\n*** SAFE MODE: image generation failed. ***")
            return

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
