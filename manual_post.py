import requests
import os
import sys


ACCESS_TOKEN = os.environ.get("LINKEDIN_ACCESS_TOKEN", "")


def get_user_id():
    url = "https://api.linkedin.com/v2/userinfo"
    headers = {"Authorization": f"Bearer {ACCESS_TOKEN}"}
    response = requests.get(url, headers=headers)
    response.raise_for_status()
    return response.json()["sub"]


def post_to_linkedin(user_id, text):
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


def main():
    post_text = os.environ.get("POST_TEXT", "")
    hashtags = os.environ.get("HASHTAGS", "")

    if not ACCESS_TOKEN:
        print("ERROR: LINKEDIN_ACCESS_TOKEN not set!")
        sys.exit(1)

    if not post_text:
        print("ERROR: POST_TEXT is empty!")
        sys.exit(1)

    # Combine post text and hashtags
    full_text = post_text.strip()
    if hashtags.strip():
        full_text += f"\n\n{hashtags.strip()}"

    print("=" * 55)
    print("LinkedIn Manual Post")
    print("=" * 55)

    user_id = get_user_id()
    print(f"Authenticated! User: {user_id}")
    print(f"Post text: {full_text[:100]}...")
    print(f"Character count: {len(full_text)}")

    status, response = post_to_linkedin(user_id, full_text)

    if status == 201:
        print(f"SUCCESS! Posted! ID: {response.get('id')}")
    else:
        print(f"FAILED! Status {status}: {response}")
        sys.exit(1)

    print("=" * 55)


if __name__ == "__main__":
    main()
