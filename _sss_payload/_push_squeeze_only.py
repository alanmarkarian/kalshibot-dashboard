"""One-off: publish ONLY squeeze.html. Never prints token. Never touches other pages."""
import base64
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

TOK = Path(r"C:\KalshiBot\github_token.txt")
# Local PC path; Actions uses squeeze.html in cwd / repo root
LOCAL_CANDIDATES = [
    Path(r"C:\Users\alanm\ShortSqueezeScreener\squeeze.html"),
    Path("squeeze.html"),
    Path(__file__).resolve().parent / "squeeze.html",
]
API = "https://api.github.com/repos/alanmarkarian/kalshibot-dashboard/contents/squeeze.html"
LOG_CANDIDATES = [
    Path(r"C:\Users\alanm\ShortSqueezeScreener\data\logs\squeeze_pages_push.log"),
    Path("data/logs/squeeze_pages_push.log"),
]

FORBIDDEN = {"index.html", "gfl.html", "social.html", "digest.html", "catalyst.html", "desk.html", "missed.html", "lottery.html"}


def _log_path():
    for p in LOG_CANDIDATES:
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            return p
        except OSError:
            continue
    return Path("squeeze_pages_push.log")


def log(msg):
    line = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%MZ ") + msg
    lp = _log_path()
    with open(lp, "a", encoding="utf-8") as f:
        f.write(line + "\n")
    print(line)


def load_token():
    """Prefer env (Actions); fall back to local token file on MarkarianPC. Never log token."""
    for name in ("PAGES_GITHUB_TOKEN", "GITHUB_TOKEN"):
        val = (os.environ.get(name) or "").strip()
        if val:
            return val
    if TOK.exists():
        val = TOK.read_text(encoding="utf-8").strip()
        if val:
            return val
    raise FileNotFoundError("token missing")


def resolve_local():
    for p in LOCAL_CANDIDATES:
        if p.exists():
            return p
    return None


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
        return json.load(resp)


def main():
    remote = "squeeze.html"
    if remote in FORBIDDEN:
        log("REFUSE: would overwrite forbidden page")
        return 1
    local = resolve_local()
    if not local:
        log("FAIL: local squeeze.html missing")
        return 1
    try:
        token = load_token()
    except (FileNotFoundError, ValueError, OSError) as e:
        log("FAIL: token missing/empty (" + type(e).__name__ + ")")
        return 1
    content = local.read_text(encoding="utf-8")
    sha = None
    try:
        sha = req(API + "?ref=main", token)["sha"]
    except urllib.error.HTTPError as e:
        if e.code != 404:
            log("FAIL GET squeeze.html: HTTP %d" % e.code)
            return 1
    body = {
        "message": "squeeze.html "
        + datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%MZ"),
        "content": base64.b64encode(content.encode()).decode(),
        "branch": "main",
    }
    if sha:
        body["sha"] = sha
    try:
        res = req(API, token, "PUT", body)
        log("PUSHED squeeze.html: %s" % res["commit"]["sha"][:7])
        return 0
    except urllib.error.HTTPError as e:
        log("FAIL PUT squeeze.html: HTTP %d" % e.code)
        return 1
    except Exception as e:
        log("FAIL PUT squeeze.html: %s" % type(e).__name__)
        return 1


if __name__ == "__main__":
    sys.exit(main())
