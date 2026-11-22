"""Privileged parent operations, executed by the root daemon on behalf of the parent app.

The parent app runs as a normal user. Before it may change anything it has to be
authorized by polkit (the desktop's own password dialog, works on X11 and Wayland).
Every request is validated here - there is deliberately no "run any command" call.
"""
import grp
import logging
import os
import pwd
import re
import subprocess
import time

import common as C
import dnsfilter

log = logging.getLogger("tuxsitter.admin")

ACTION_ID = "io.github.chpusch.tuxsitter.manage"
SESSION_MAX = 12 * 3600          # an authorized parent app stays authorized at most this long
USERNAME_RE = re.compile(r"^[a-z][a-z0-9_-]{1,30}$")
HM_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$|^24:00$")
CUSTOM_DNS_RE = re.compile(r"^[0-9A-Fa-f:.,; ]{0,200}$")


class AdminError(Exception):
    """Carries an error code for the client (see common.ERRORS)."""


# ---------------------------------------------------------------------------
# polkit authorization
# ---------------------------------------------------------------------------
def process_start_time(pid):
    """Start time of a process in clock ticks (field 22 of /proc/<pid>/stat).
    Together with the PID it identifies a process uniquely (PIDs get reused)."""
    try:
        with open("/proc/%d/stat" % pid) as f:
            data = f.read()
        return int(data.rsplit(")", 1)[1].split()[19])
    except (OSError, ValueError, IndexError):
        return None


class Authorizer:
    def __init__(self, pkcheck="pkcheck"):
        self.pkcheck = pkcheck
        self.sessions = {}     # (pid, start_time) -> (uid, expires)
        self.failures = {}     # uid -> [count, locked_until]

    def is_authorized(self, pid, uid):
        if uid == 0:
            return True
        if not pid:
            return False
        key = (pid, process_start_time(pid))
        s = self.sessions.get(key)
        if s and s[0] == uid and s[1] > time.time():
            return True
        self.sessions.pop(key, None)
        return False

    def authorize(self, pid, uid):
        """Ask polkit (shows the password dialog). Raises AdminError on failure."""
        if self.is_authorized(pid, uid):
            return
        f = self.failures.get(uid, [0, 0])
        if f[1] > time.time():
            raise AdminError("locked_out")
        start = process_start_time(pid) if pid else None
        if start is None:
            raise AdminError("forbidden")
        try:
            r = subprocess.run(
                [self.pkcheck, "--action-id", ACTION_ID,
                 "--process", "%d,%d,%d" % (pid, start, uid), "--allow-user-interaction"],
                capture_output=True, text=True, timeout=300)
        except (OSError, subprocess.TimeoutExpired) as e:
            log.error("pkcheck failed: %s", e)
            raise AdminError("auth_unavailable")
        if r.returncode != 0:
            # 1 = not authorized / wrong password, 3 = dialog dismissed
            if r.returncode == 1:
                f[0] += 1
                if f[0] >= 3:
                    f[1] = time.time() + min(3600, 30 * 2 ** (f[0] - 3))
                self.failures[uid] = f
            raise AdminError("auth_failed")
        self.failures.pop(uid, None)
        # forget sessions of processes that are gone
        self.sessions = {k: v for k, v in self.sessions.items()
                         if v[1] > time.time() and process_start_time(k[0]) == k[1]}
        self.sessions[(pid, start)] = (uid, time.time() + SESSION_MAX)
        log.info("Parent app (pid %d, uid %d) authorized", pid, uid)


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------
def admin_groups_of(user):
    res = []
    for g in ("sudo", "admin", "wheel"):
        try:
            if user in grp.getgrnam(g).gr_mem:
                res.append(g)
        except KeyError:
            pass
    return res


def real_name(user):
    try:
        return pwd.getpwnam(user).pw_gecos.split(",")[0].strip() or user
    except KeyError:
        return user


def candidate_users(exclude=None):
    """Normal login accounts that could be a child account."""
    out = []
    for p in pwd.getpwall():
        if 1000 <= p.pw_uid < 60000 and not p.pw_shell.endswith(("nologin", "false")) \
                and p.pw_name != exclude:
            out.append({"user": p.pw_name, "name": real_name(p.pw_name),
                        "admin": bool(admin_groups_of(p.pw_name))})
    return sorted(out, key=lambda u: u["name"].lower())


def user_exists(user):
    try:
        pwd.getpwnam(user)
        return True
    except KeyError:
        return False


def create_account(user, name, password):
    if not USERNAME_RE.match(user or "") or user_exists(user):
        raise AdminError("bad_username")
    name = re.sub(r"[,:=\n\r\t]", " ", (name or user)).strip()[:60] or user
    if not isinstance(password, str) or len(password) < 4 or "\n" in password or len(password) > 200:
        raise AdminError("bad_password")
    if os.path.exists("/usr/sbin/adduser"):
        cmd = ["adduser", "--disabled-password", "--gecos", name, user]
    else:
        cmd = ["useradd", "-m", "-s", "/bin/bash", "-c", name, user]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        log.error("account creation failed: %s", r.stderr.strip())
        raise AdminError("account_failed")
    r = subprocess.run(["chpasswd"], input="%s:%s\n" % (user, password), capture_output=True, text=True)
    if r.returncode != 0:
        raise AdminError("account_failed")
    log.info("Created child account %s", user)


def remove_admin_rights(user):
    for g in admin_groups_of(user):
        subprocess.run(["gpasswd", "-d", user, g], capture_output=True)
    log.info("Removed administrator rights of %s", user)


# ---------------------------------------------------------------------------
# Validation of settings sent by the parent app
# ---------------------------------------------------------------------------
def _bool(v):
    if not isinstance(v, bool):
        raise AdminError("invalid")
    return v


def _clean_day(d):
    out = dict(C.DEFAULT_DAY)
    out["limit_enabled"] = _bool(d.get("limit_enabled", out["limit_enabled"]))
    lm = d.get("limit_min", out["limit_min"])
    if not isinstance(lm, int) or isinstance(lm, bool) or not 0 <= lm <= 1440:
        raise AdminError("invalid")
    out["limit_min"] = lm
    out["window_enabled"] = _bool(d.get("window_enabled", out["window_enabled"]))
    for k in ("start", "end"):
        v = d.get(k, out[k])
        if not isinstance(v, str) or not HM_RE.match(v):
            raise AdminError("invalid")
        out[k] = v
    return out


def clean_child(c):
    if not isinstance(c, dict):
        raise AdminError("invalid")
    out = C.new_child_config()
    out["enabled"] = _bool(c.get("enabled", True))
    out["holiday_mode"] = _bool(c.get("holiday_mode", False))
    for key in ("school", "weekend"):
        out[key] = _clean_day(c.get(key) or {})
    d = c.get("dns") or {}
    dns = out["dns"]
    dns["enabled"] = _bool(d.get("enabled", dns["enabled"]))
    if d.get("provider", dns["provider"]) not in C.PROVIDERS:
        raise AdminError("invalid")
    dns["provider"] = d.get("provider", dns["provider"])
    nid = d.get("nextdns_id", "")
    if not isinstance(nid, str) or not re.match(r"^[A-Za-z0-9]{0,32}$", nid.strip()):
        raise AdminError("bad_nextdns_id")
    dns["nextdns_id"] = nid.strip()
    cus = d.get("custom", "")
    if not isinstance(cus, str) or not CUSTOM_DNS_RE.match(cus):
        raise AdminError("bad_custom_dns")
    dns["custom"] = cus.strip()
    dns["safesearch"] = _bool(d.get("safesearch", True))
    if d.get("youtube", "moderate") not in ("off", "moderate", "strict"):
        raise AdminError("invalid")
    dns["youtube"] = d.get("youtube", "moderate")
    dns["block_bypass"] = _bool(d.get("block_bypass", True))
    return out


def apply_settings(cfg, new):
    """Update the settings of existing children and global options. Children are added and
    removed with separate commands; the PIN only via set_pin."""
    if not isinstance(new, dict) or not isinstance(new.get("children", {}), dict):
        raise AdminError("invalid")
    for user, c in new.get("children", {}).items():
        if user not in cfg["children"]:
            raise AdminError("unknown_child")
        cfg["children"][user] = clean_child(c)
    if "update_check" in new:
        cfg["update_check"] = _bool(new["update_check"])
    return cfg


def public_config(cfg):
    """Configuration as the parent app may see it (no PIN hash)."""
    out = {k: v for k, v in cfg.items() if k != "pin"}
    out["pin_set"] = bool(cfg.get("pin"))
    return out


def dns_test(dns_cfg):
    try:
        return {"ok": True, "ms": dnsfilter.test_upstream(clean_child({"dns": dns_cfg})["dns"])}
    except AdminError as e:
        return {"ok": False, "error": str(e)}
    except ValueError as e:
        return {"ok": False, "error": str(e)}
    except LookupError:
        return {"ok": False, "error": "no_answer"}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": "no_answer", "detail": str(e)}
