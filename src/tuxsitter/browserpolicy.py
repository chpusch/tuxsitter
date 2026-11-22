"""Lock the "secure DNS" / DNS-over-HTTPS setting of installed browsers.

Browsers read their enterprise policies from root-owned system folders, so a child
cannot change them. These policies apply to ALL accounts on the computer; we only
switch off the browser's own DNS (it then uses the system DNS like every other program),
so for the parents nothing changes in practice.

Limits: browsers installed by the child itself as Flatpak (or a portable download)
do not read these system folders.
"""
import hashlib
import json
import logging
import os

import common as C

log = logging.getLogger("tuxsitter.browser")

FIREFOX = "/etc/firefox/policies/policies.json"
FIREFOX_POLICY = {"DNSOverHTTPS": {"Enabled": False, "Locked": True}}

CHROMIUM_DIRS = [
    "/etc/opt/chrome/policies/managed",        # Google Chrome
    "/etc/chromium/policies/managed",          # Chromium (Debian, Linux Mint)
    "/etc/chromium-browser/policies/managed",  # Chromium (older Ubuntu)
    "/etc/brave/policies/managed",             # Brave
    "/etc/opt/edge/policies/managed",          # Microsoft Edge
    "/etc/opt/vivaldi/policies/managed",       # Vivaldi
]
CHROMIUM_POLICY = {"DnsOverHttpsMode": "off"}
CHROMIUM_FILE = "tuxsitter.json"


def _sha(path):
    try:
        with open(path, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()
    except OSError:
        return None


def _read(path):
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except OSError:
        return None


def apply(enabled, state):
    """Write or remove the policies. `state` is a dict persisted by the daemon:
    {path: {"sha": <hash of what we wrote>, "backup": <original file content or None>}}"""
    if enabled:
        _enable(state)
    else:
        disable(state)


def _write(path, data, state, backup=None):
    C.save_json(path, data, 0o644)
    for d in (os.path.dirname(path), os.path.dirname(os.path.dirname(path))):
        try:
            os.chmod(d, 0o755)
        except OSError:
            pass
    entry = state.setdefault(path, {"backup": backup})
    entry["sha"] = _sha(path)


def _enable(state):
    # Firefox: one shared file - merge into an existing policies.json instead of replacing it
    try:
        ours = state.get(FIREFOX)
        if ours and ours.get("sha") == _sha(FIREFOX):
            backup = ours.get("backup")                # our own file: rebuild from the original
        else:
            backup = _read(FIREFOX)                    # no file, or someone else's: keep it
            state.pop(FIREFOX, None)
        data = json.loads(backup) if backup else {"policies": {}}
        data.setdefault("policies", {}).update(FIREFOX_POLICY)
        _write(FIREFOX, data, state, backup)
    except (OSError, ValueError) as e:
        log.warning("Firefox policy: %s", e)
    # Chromium family: our own file in each managed-policy folder
    for d in CHROMIUM_DIRS:
        path = os.path.join(d, CHROMIUM_FILE)
        try:
            if _sha(path) != (state.get(path) or {}).get("sha"):
                state.pop(path, None)
            _write(path, CHROMIUM_POLICY, state)
        except OSError as e:
            log.warning("Browser policy %s: %s", path, e)


def disable(state):
    """Remove our policies again (restoring a Firefox policies.json that existed before)."""
    for path in list(state):
        entry = state[path]
        if _sha(path) == entry.get("sha"):           # untouched since we wrote it
            try:
                if entry.get("backup"):
                    with open(path, "w", encoding="utf-8") as f:
                        f.write(entry["backup"])
                else:
                    os.unlink(path)
            except OSError as e:
                log.warning("Cannot remove browser policy %s: %s", path, e)
        state.pop(path, None)
