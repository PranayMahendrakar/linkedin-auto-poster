"""
Renew the LinkedIn access token and save it to the GitHub Actions secret.

1. Opens LinkedIn sign-in in your browser.
2. Catches the redirect on http://localhost:8080/callback.
3. Exchanges the code for an access token (once).
4. Checks the token against LinkedIn's /v2/userinfo.
5. Saves it to the LINKEDIN_ACCESS_TOKEN secret with the GitHub CLI.

The token is never printed or written to disk.

Usage:
    python renew_linkedin_token.py             # renew and save to GitHub
    python renew_linkedin_token.py --no-save   # renew and test only

The client secret is read from the LINKEDIN_CLIENT_SECRET environment
variable, or asked for (hidden) if that isn't set.
Needs Python 3.8+ (standard library only) and `gh` signed in.
"""

import argparse
import getpass
import html
import json
import os
import secrets
import subprocess
import sys
import time
import webbrowser
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import Request, urlopen


# ============ CONFIGURATION ============
CLIENT_ID = "86ud75byr8690x"
REDIRECT_URI = "http://localhost:8080/callback"   # must match the app's Auth settings
SCOPES = "openid profile email w_member_social"
REPO = "PranayMahendrakar/linkedin-auto-poster"
SECRET_NAME = "LINKEDIN_ACCESS_TOKEN"

AUTH_URL = "https://www.linkedin.com/oauth/v2/authorization"
TOKEN_URL = "https://www.linkedin.com/oauth/v2/accessToken"
USERINFO_URL = "https://api.linkedin.com/v2/userinfo"
SIGN_IN_TIMEOUT = 300  # seconds to wait for you to approve in the browser


def fail(message):
    print(f"\nERROR: {message}")
    sys.exit(1)


# ============ STEP 1-2: SIGN IN ============
class CallbackServer(HTTPServer):
    allow_reuse_address = False  # on Windows, True would let two programs share the port


def get_auth_code(state):
    redirect = urlparse(REDIRECT_URI)
    result = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            url = urlparse(self.path)
            if url.path != redirect.path:
                self.send_response(404)
                self.end_headers()
                return
            query = {k: v[0] for k, v in parse_qs(url.query).items()}
            if query.get("state") != state:
                result["error"] = "the sign-in response didn't match this run (state mismatch)"
            elif "error" in query:
                result["error"] = f"{query['error']}: {query.get('error_description', '')}"
            elif "code" in query:
                result["code"] = query["code"]
            else:
                result["error"] = "LinkedIn redirected back without a code"

            if "code" in result:
                body = "<h2>Signed in.</h2><p>You can close this tab and go back to the terminal.</p>"
            else:
                body = f"<h2>Sign-in failed</h2><p>{html.escape(result['error'])}</p>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(body.encode("utf-8"))

        def log_message(self, *args):
            pass

    try:
        server = CallbackServer(("127.0.0.1", redirect.port), Handler)
    except OSError:
        fail(f"port {redirect.port} is already in use. Close whatever is using it and try again.")
    server.timeout = 1

    auth_url = AUTH_URL + "?" + urlencode({
        "response_type": "code",
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "state": state,
        "scope": SCOPES,
    })
    print("Opening LinkedIn sign-in in your browser...")
    print(f"If it doesn't open, paste this into your browser:\n{auth_url}\n")
    webbrowser.open(auth_url)

    deadline = time.monotonic() + SIGN_IN_TIMEOUT
    try:
        while not result and time.monotonic() < deadline:
            server.handle_request()
    finally:
        server.server_close()

    if not result:
        fail(f"no sign-in received within {SIGN_IN_TIMEOUT // 60} minutes.")
    if "error" in result:
        fail(f"LinkedIn sign-in failed: {result['error']}")
    return result["code"]


# ============ STEP 3-4: TOKEN ============
def fetch_json(request):
    try:
        with urlopen(request, timeout=30) as response:
            return response.status, json.load(response)
    except HTTPError as e:
        try:
            return e.code, json.load(e)
        except ValueError:
            return e.code, {}
    except URLError as e:
        fail(f"couldn't reach {request.full_url}: {e.reason}")


def exchange_code(code, client_secret):
    data = urlencode({
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": REDIRECT_URI,
        "client_id": CLIENT_ID,
        "client_secret": client_secret,
    }).encode()
    request = Request(TOKEN_URL, data=data,
                      headers={"Content-Type": "application/x-www-form-urlencoded"})
    status, body = fetch_json(request)
    if status != 200 or "access_token" not in body:
        detail = body.get("error_description") or body.get("error") or f"HTTP {status}"
        fail(f"LinkedIn didn't issue a token: {detail}")
    return body


def check_token(token):
    request = Request(USERINFO_URL, headers={"Authorization": f"Bearer {token}"})
    status, body = fetch_json(request)
    if status != 200:
        fail(f"LinkedIn rejected the new token (HTTP {status}). Not saving it.")
    return body


# ============ STEP 5: GITHUB ============
def gh(*args, input_text=None):
    try:
        return subprocess.run(["gh", *args], input=input_text, text=True,
                              capture_output=True, check=True).stdout.strip()
    except FileNotFoundError:
        fail("GitHub CLI (gh) not found. Install it from https://cli.github.com and run `gh auth login`.")
    except subprocess.CalledProcessError as e:
        fail(f"`gh {args[0]} {args[1]}` failed: {e.stderr.strip()}")


def save_secret(token):
    # Value goes in on stdin so it never shows up in the process list.
    gh("secret", "set", SECRET_NAME, "-R", REPO, input_text=token)
    return gh("api", f"repos/{REPO}/actions/secrets/{SECRET_NAME}", "--jq", ".updated_at")


# ============ MAIN ============
def main():
    parser = argparse.ArgumentParser(description="Renew the LinkedIn token for the auto-poster.")
    parser.add_argument("--no-save", action="store_true",
                        help="get and test a token, but don't update the GitHub secret")
    args = parser.parse_args()

    print("=" * 55)
    print("LinkedIn token renewal")
    print("=" * 55)

    client_secret = os.environ.get("LINKEDIN_CLIENT_SECRET", "").strip()
    if not client_secret:
        client_secret = getpass.getpass("LinkedIn app client secret (typing is hidden): ").strip()
    if not client_secret:
        fail("no client secret given.")

    code = get_auth_code(secrets.token_urlsafe(24))
    print("[1/3] Sign-in approved. Exchanging code for a token...")

    token_info = exchange_code(code, client_secret)
    token = token_info["access_token"].strip()
    expires = datetime.now() + timedelta(seconds=int(token_info.get("expires_in", 0)))

    user = check_token(token)
    print(f"[2/3] Token works. Signed in as {user.get('name', user.get('sub', 'unknown'))}.")
    print(f"      Scopes: {token_info.get('scope', '?')}")
    print(f"      Expires: {expires:%Y-%m-%d} (renew a few days before)")

    if args.no_save:
        print("[3/3] --no-save given. GitHub secret NOT updated.")
        return

    updated_at = save_secret(token)
    print(f"[3/3] Saved to {SECRET_NAME} in {REPO} (updated {updated_at}).")
    print("\nDone. The next workflow run will use the new token.")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        fail("cancelled.")
