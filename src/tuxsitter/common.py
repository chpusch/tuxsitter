"""Shared helpers for Tuxsitter (daemon, parent app, child panel indicator)."""
import copy
import hashlib
import hmac
import json
import os
import secrets
import socket
import tempfile
import time

from i18n import _, N_

APP_NAME = "Tuxsitter"
APP_ID = "io.github.chpusch.Tuxsitter"
VERSION = "0.4.3"
HOMEPAGE = "https://github.com/chpusch/tuxsitter"

CONFIG_DIR = os.environ.get("TUXSITTER_CONFIG_DIR", "/etc/tuxsitter")
STATE_DIR = os.environ.get("TUXSITTER_STATE_DIR", "/var/lib/tuxsitter")
RUN_DIR = os.environ.get("TUXSITTER_RUN_DIR", "/run/tuxsitter")
CONFIG_FILE = os.path.join(CONFIG_DIR, "config.json")
STATE_FILE = os.path.join(STATE_DIR, "state.json")
SOCKET_PATH = os.path.join(RUN_DIR, "tuxsitter.sock")

DNS_PORT_BASE = 20000  # child with UID 1001 -> local port 21001

# ---------------------------------------------------------------------------
# DNS providers - all built-in providers are queried encrypted (DNS-over-HTTPS).
# Endpoints from the providers' own documentation; check them live with
#   python3 tools/check-providers.py
# ---------------------------------------------------------------------------
PROVIDERS = {
    "cloudflare": {
        "name": "Cloudflare for Families",
        "desc": N_("Blocks adult content and malware, but not VPNs or other DNS services. Free and very fast."),
        "doh": "https://family.cloudflare-dns.com/dns-query",
    },
    "cleanbrowsing": {
        "name": "CleanBrowsing Family",
        "desc": N_("Stricter: also blocks VPNs, proxies and other DNS services that could bypass the filter, and sites like Reddit. Recommended for older children. Free."),
        "doh": "https://doh.cleanbrowsing.org/doh/family-filter/",
    },
    "adguard": {
        "name": "AdGuard Family",
        "desc": N_("Blocks adult content and also ads. Free."),
        "doh": "https://family.adguard-dns.com/dns-query",
    },
    "opendns": {
        "name": "OpenDNS FamilyShield",
        "desc": N_("Recommended: blocks adult content and also VPNs, proxies and other DNS services that could bypass the filter. Free."),
        "doh": "https://familyshield.opendns.com/dns-query",
    },
    "nextdns": {
        "name": N_("NextDNS (your own profile)"),
        "desc": N_("Configure everything yourself at my.nextdns.io and enter the profile ID here."),
        "doh": "https://dns.nextdns.io/{id}/{device}",
    },
    "custom": {
        "name": N_("Custom DNS server"),
        "desc": N_("IP address(es) of your own filter, e.g. Pi-hole. Separate several with commas."),
    },
}
PROVIDER_ORDER = ["opendns", "cleanbrowsing", "cloudflare", "adguard", "nextdns", "custom"]

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------
DEFAULT_DAY = {
    "limit_enabled": True,
    "limit_min": 90,
    "window_enabled": True,
    "start": "07:00",
    "end": "20:00",
}

DEFAULT_CHILD = {
    "enabled": True,
    "holiday_mode": False,
    "school": dict(DEFAULT_DAY),
    "weekend": dict(DEFAULT_DAY, limit_min=150, start="08:00", end="21:00"),
    "dns": {
        "enabled": True,
        "provider": "opendns",
        "nextdns_id": "",
        "custom": "",
        "safesearch": True,
        "youtube": "moderate",   # off | moderate | strict
        "block_bypass": True,
    },
}

DEFAULT_CONFIG = {
    "version": 2,
    "pin": None,
    "update_check": True,   # look for a newer release on GitHub once a day
    "browser_doh_lock": True,   # switch off the browsers' own DNS (policies for all accounts)
    "children": {},
}


def new_child_config():
    return copy.deepcopy(DEFAULT_CHILD)


def _merge(defaults, data):
    out = copy.deepcopy(defaults)
    if isinstance(data, dict):
        for k, v in data.items():
            if k in out and isinstance(out[k], dict) and isinstance(v, dict):
                out[k] = _merge(out[k], v)
            else:
                out[k] = v
    return out


def load_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return copy.deepcopy(default)


def save_json(path, data, mode=0o600):
    d = os.path.dirname(path)
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".tmp-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


CONFIG_VERSION = 2


def migrate_config(cfg):
    """Bring an older config up to CONFIG_VERSION. Returns True if something changed."""
    try:
        version = int(cfg.get("version", 1))
    except (TypeError, ValueError):
        version = 1
    if version >= CONFIG_VERSION:
        return False
    if version < 2:
        # 0.4.1: the default filter changed from Cloudflare for Families to OpenDNS FamilyShield,
        # which also blocks other DNS services, VPN and proxy sites. Before 0.4.1 "cloudflare" was
        # the default; a deliberate choice cannot be told apart from it, so all are switched.
        for child in cfg.get("children", {}).values():
            dns = child.get("dns", {})
            if dns.get("provider") == "cloudflare":
                dns["provider"] = "opendns"
    cfg["version"] = CONFIG_VERSION
    return True


def load_config():
    raw = load_json(CONFIG_FILE, None)
    cfg = _merge(DEFAULT_CONFIG, raw if raw is not None else DEFAULT_CONFIG)
    if raw is not None and "version" not in raw:
        cfg["version"] = 1
    cfg.pop("browser_policies", None)
    cfg["children"] = {u: _merge(DEFAULT_CHILD, c) for u, c in cfg.get("children", {}).items()}
    if migrate_config(cfg) and raw is not None:
        try:
            save_config(cfg)          # only root can write; otherwise migrate again next time
        except OSError:
            pass
    return cfg


def save_config(cfg):
    save_json(CONFIG_FILE, cfg, 0o600)


# ---------------------------------------------------------------------------
# Parent PIN
# ---------------------------------------------------------------------------
PIN_ITER = 200_000


def hash_pin(pin):
    salt = secrets.token_bytes(16)
    h = hashlib.pbkdf2_hmac("sha256", pin.encode(), salt, PIN_ITER)
    return {"salt": salt.hex(), "hash": h.hex(), "iter": PIN_ITER}


def check_pin(stored, pin):
    if not stored or not pin:
        return False
    try:
        salt = bytes.fromhex(stored["salt"])
        h = hashlib.pbkdf2_hmac("sha256", str(pin).encode(), salt, int(stored.get("iter", PIN_ITER)))
        return hmac.compare_digest(h.hex(), stored["hash"])
    except (KeyError, ValueError, TypeError):
        return False


# ---------------------------------------------------------------------------
# Time helpers
# ---------------------------------------------------------------------------
def hm_to_min(s):
    h, m = s.split(":")
    return int(h) * 60 + int(m)


def min_to_hm(m):
    m = max(0, min(int(m), 24 * 60))
    if m == 24 * 60:
        return "24:00"
    return "%02d:%02d" % (m // 60, m % 60)


def fmt_duration(seconds, short=False):
    """'1 h 5 min' / '45 min' / '30 s' (translated), or '1:05' when short."""
    seconds = max(0, int(seconds))
    if seconds < 60 and not short:
        return _("{s} s").format(s=seconds)
    m = (seconds + 59) // 60
    h, m = divmod(m, 60)
    if short:
        return "%d:%02d" % (h, m)
    if h and m:
        return _("{h} h {m} min").format(h=h, m=m)
    if h:
        return _("{h} h").format(h=h)
    return _("{m} min").format(m=m)


def day_rules(child_cfg, t=None):
    """Rules for the given day: school day or weekend/holiday."""
    lt = time.localtime(t)
    if child_cfg.get("holiday_mode") or lt.tm_wday >= 5:
        return child_cfg["weekend"], "weekend"
    return child_cfg["school"], "school"


def next_text(st):
    """'today from 07:00' / 'tomorrow from 07:00' from a daemon status dict."""
    if not st.get("next_time"):
        return ""
    if st.get("next_day") == "today":
        return _("today from {time}").format(time=st["next_time"])
    return _("tomorrow from {time}").format(time=st["next_time"])


ERRORS = {
    "invalid": N_("Invalid request."),
    "locked_out": N_("Too many attempts. Please try again in {time}."),
    "no_pin": N_("No parent PIN has been set yet."),
    "wrong_pin": N_("Wrong PIN."),
    "forbidden": N_("Permission denied."),
    "unknown_child": N_("Unknown child."),
    "unknown_cmd": N_("Unknown command."),
    "internal": N_("Internal error."),
    "unreachable": N_("The Tuxsitter service is not reachable."),
    "bad_nextdns_id": N_("Invalid NextDNS profile ID."),
    "bad_custom_dns": N_("Invalid DNS server address."),
    "no_answer": N_("The filter did not return an address."),
    "auth_required": N_("Please confirm with your password first."),
    "auth_failed": N_("Authentication failed."),
    "auth_unavailable": N_("The password check (polkit) is not available on this system."),
    "bad_username": N_("Invalid or already existing username."),
    "bad_password": N_("The password must have at least 4 characters."),
    "account_failed": N_("The account could not be created."),
}


def error_text(resp):
    """Human readable, translated error from a daemon response."""
    resp = resp or {"error": "unreachable"}
    code = resp.get("error", "internal")
    msg = _(ERRORS.get(code, ERRORS["internal"]))
    if code == "locked_out":
        msg = msg.format(time=fmt_duration(resp.get("retry_in", 60)))
    if resp.get("detail") and code in ("internal", "no_answer"):
        msg += " (%s)" % resp["detail"]
    return msg


# ---------------------------------------------------------------------------
# Socket client (indicator + parent app)
# ---------------------------------------------------------------------------
def request(msg, timeout=5.0, path=None):
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect(path or SOCKET_PATH)
        s.sendall(json.dumps(msg).encode() + b"\n")
        buf = b""
        while not buf.endswith(b"\n"):
            chunk = s.recv(65536)
            if not chunk:
                break
            buf += chunk
        return json.loads(buf.decode() or "{}")
    finally:
        s.close()
