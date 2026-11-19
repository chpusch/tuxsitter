"""Check GitHub for a newer Tuxsitter release (at most once a day, can be switched off).

Only one anonymous request to the public GitHub API is made - no ID, no statistics.
"""
import json
import re
import time
import urllib.request

import common as C

API_URL = "https://api.github.com/repos/chpusch/tuxsitter/releases?per_page=10"
CACHE_FILE = C.STATE_DIR + "/update.json"
INTERVAL = 24 * 3600


def parse_version(v):
    """'v0.10.2' -> (0, 10, 2); unknown -> ()"""
    m = re.match(r"^v?(\d+(?:\.\d+)*)", (v or "").strip())
    return tuple(int(x) for x in m.group(1).split(".")) if m else ()


def newest_release(releases):
    """Pick the highest published (non-draft) release from the GitHub API answer."""
    best = None
    for r in releases or []:
        if r.get("draft"):
            continue
        ver = parse_version(r.get("tag_name"))
        if ver and (best is None or ver > best[0]):
            best = (ver, r.get("tag_name", "").lstrip("v"), r.get("html_url") or C.HOMEPAGE + "/releases")
    return (best[1], best[2]) if best else (None, None)


def fetch(timeout=6):
    req = urllib.request.Request(API_URL, headers={
        "Accept": "application/vnd.github+json",
        "User-Agent": "Tuxsitter/%s" % C.VERSION,
    })
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return newest_release(json.loads(resp.read().decode("utf-8")))


def check(force=False):
    """Return (version, url) if a newer version than the installed one exists, else None.
    Uses a cached answer if the last check was less than a day ago."""
    cache = C.load_json(CACHE_FILE, {})
    if force or time.time() - cache.get("checked", 0) > INTERVAL:
        try:
            latest, url = fetch()
            cache = {"checked": time.time(), "latest": latest, "url": url}
            C.save_json(CACHE_FILE, cache, 0o600)
        except Exception:  # noqa: BLE001 - offline etc.: try again next time
            return None
    latest = cache.get("latest")
    if latest and parse_version(latest) > parse_version(C.VERSION):
        return latest, cache.get("url") or C.HOMEPAGE + "/releases"
    return None
