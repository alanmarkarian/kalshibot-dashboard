"""One-off: publish ONLY desk.html. Never prints token. Never touches other pages."""
import base64
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

TOK = Path(r"C:\KalshiBot\github_token.txt")
LOCAL = Path(r"C:\Users\alanm\ShortSqueezeScreener\desk.html")
API = "https://api.github.com/repos/alanmarkarian/kalshibot-dashboard/contents/desk.html"
LOG = Path(r"C:\Users\alanm\ShortSqueezeScreener\data\logs\desk_pages_push.log")

FORBIDDEN = {
    "index.html",
    "squeeze.html",
    "social.html",
    "gfl.html",
    "missed.html",
    "digest.html",
    "catalyst.html",
}


def log(msg):
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(msg + "\n")
    print(msg)


def req(url, token, method="GET", body=None):
    r = urllib.request.Request(
        url,
        method=method,
        data=json.dumps(body).encode() if body else None,
    )
    r.add_header("Authorization", "Bearer " + token)
    r.add_header("User-Agent", "kalshibot-pages")
    r.add_header("Accept", "application/vnd.github+json")
    if body:
        r.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(r, timeout=60) as resp:
        return json.load(resp), resp.status


def main():
    remote = "desk.html"
    if remote in FORBIDDEN:
        log("REFUSE: would overwrite forbidden page")
        return 1
    if not LOCAL.exists():
        log("FAIL: local desk.html missing")
        return 1
    try:
        token = TOK.read_text(encoding="utf-8").strip()
        if not token:
            raise ValueError("empty token file")
    except (FileNotFoundError, ValueError):
        log("FAIL: token missing/empty")
        return 1
    content = LOCAL.read_text(encoding="utf-8")
    sha = None
    try:
        data, status = req(API + "?ref=main", token)
        sha = data.get("sha")
        log("GET desk.html: HTTP %s" % status)
    except urllib.error.HTTPError as e:
        if e.code != 404:
            log("FAIL GET desk.html: HTTP %d" % e.code)
            return 1
        log("GET desk.html: HTTP 404 (new file)")
    body = {
        "message": "desk.html 2026-08-21 Squeeze Desk",
        "content": base64.b64encode(content.encode()).decode(),
        "branch": "main",
    }
    if sha:
        body["sha"] = sha
    try:
        res, status = req(API, token, "PUT", body)
        sha7 = res.get("commit", {}).get("sha", "")[:7]
        log("PUT desk.html: HTTP %s commit %s" % (status, sha7))
        return 0
    except urllib.error.HTTPError as e:
        log("FAIL PUT desk.html: HTTP %d" % e.code)
        return 1
    except Exception:
        log("FAIL PUT desk.html: error")
        return 1


if __name__ == "__main__":
    sys.exit(main())
