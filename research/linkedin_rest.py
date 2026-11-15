"""
LinkedIn document posts through the current (versioned) REST API.

The news poster uses the older /v2/ugcPosts API, which can't carry a PDF. This
uses the Documents API + Posts API instead. The existing LINKEDIN_ACCESS_TOKEN
works as is: w_member_social covers documents owned by your own profile.

    1. GET  /v2/userinfo                              -> your person URN
    2. POST /rest/documents?action=initializeUpload   -> upload URL + document URN
    3. PUT  <upload URL>                              (the PDF bytes)
    4. GET  /rest/documents/<urn>                     until status is AVAILABLE
    5. POST /rest/posts                               the post, with the document attached

The Sunday roundup is a plain text post (step 5 without a document).
"""

import os
import sys
import time
from urllib.parse import quote

import requests

from research import config


class TokenRejected(Exception):
    pass


class LinkedInError(Exception):
    pass


def token():
    value = os.environ.get("LINKEDIN_ACCESS_TOKEN", "").strip()
    if not value:
        raise LinkedInError("LINKEDIN_ACCESS_TOKEN is not set")
    return value


def rest_headers(json_body=True):
    headers = {
        "Authorization": f"Bearer {token()}",
        "Linkedin-Version": config.LINKEDIN_VERSION,
        "X-Restli-Protocol-Version": "2.0.0",
    }
    if json_body:
        headers["Content-Type"] = "application/json"
    return headers


def check(response, what):
    if response.status_code == 401:
        raise TokenRejected(
            "LinkedIn rejected LINKEDIN_ACCESS_TOKEN (401 Unauthorized). It has expired "
            "(tokens last 60 days) or was revoked. Renew it with: python renew_linkedin_token.py")
    if response.status_code == 426 or "NONEXISTENT_VERSION" in response.text or "is not active" in response.text:
        raise LinkedInError(f"{what}: LinkedIn-Version {config.LINKEDIN_VERSION} was refused "
                            f"({response.text[:200]}). Bump LINKEDIN_VERSION in research/config.py.")
    if response.status_code >= 300:
        raise LinkedInError(f"{what} failed: HTTP {response.status_code} {response.text[:400]}")
    return response


def person_urn():
    r = requests.get(f"{config.LINKEDIN_API}/v2/userinfo",
                     headers={"Authorization": f"Bearer {token()}"}, timeout=30)
    check(r, "Reading your LinkedIn profile")
    return f"urn:li:person:{r.json()['sub']}"


def upload_document(owner, pdf_path):
    size = os.path.getsize(pdf_path)
    if size > config.DOC_MAX_BYTES:
        raise LinkedInError(f"{pdf_path} is {size // (1024 * 1024)} MB; LinkedIn's limit is 100 MB")
    r = requests.post(f"{config.LINKEDIN_API}/rest/documents?action=initializeUpload",
                      headers=rest_headers(),
                      json={"initializeUploadRequest": {"owner": owner}}, timeout=30)
    value = check(r, "Starting the PDF upload").json()["value"]
    with open(pdf_path, "rb") as f:
        put = requests.put(value["uploadUrl"], data=f.read(),
                           headers={"Authorization": f"Bearer {token()}",
                                    "Content-Type": "application/octet-stream"},
                           timeout=300)
    check(put, "Uploading the PDF")
    return value["document"]


def wait_until_available(document_urn, timeout=300):
    url = f"{config.LINKEDIN_API}/rest/documents/{quote(document_urn, safe='')}"
    deadline = time.time() + timeout
    status = "?"
    while time.time() < deadline:
        r = requests.get(url, headers=rest_headers(json_body=False), timeout=30)
        status = check(r, "Checking the PDF").json().get("status", "?")
        if status == "AVAILABLE":
            return
        if status == "PROCESSING_FAILED":
            raise LinkedInError("LinkedIn could not process the PDF (PROCESSING_FAILED)")
        time.sleep(5)
    raise LinkedInError(f"The PDF was still {status} after {timeout}s")


def create_post(author, commentary, document_urn=None, title=""):
    payload = {
        "author": author,
        "commentary": commentary,
        "visibility": "PUBLIC",
        "distribution": {"feedDistribution": "MAIN_FEED", "targetEntities": [],
                         "thirdPartyDistributionChannels": []},
        "lifecycleState": "PUBLISHED",
        "isReshareDisabledByAuthor": False,
    }
    if document_urn:
        payload["content"] = {"media": {"title": title[:200], "id": document_urn}}
    r = requests.post(f"{config.LINKEDIN_API}/rest/posts", headers=rest_headers(), json=payload, timeout=60)
    check(r, "Creating the post")
    return r.headers.get("x-restli-id") or r.headers.get("X-RestLi-Id") or ""


def publish_document_post(commentary, pdf_path, title):
    """Upload the PDF and publish the post. Returns the post URN."""
    author = person_urn()
    print(f"[LinkedIn] Signed in as {author}")
    document = upload_document(author, pdf_path)
    print(f"[LinkedIn] Uploaded {os.path.basename(pdf_path)} as {document}; waiting for processing...")
    wait_until_available(document)
    urn = create_post(author, commentary, document, title)
    print(f"[LinkedIn] Published {urn}")
    return urn


def publish_text_post(commentary):
    """A text-only post (the Sunday roundup). Returns the post URN."""
    author = person_urn()
    urn = create_post(author, commentary)
    print(f"[LinkedIn] Published {urn}")
    return urn


if __name__ == "__main__":
    print(person_urn())
    sys.exit(0)
