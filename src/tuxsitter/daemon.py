#!/usr/bin/python3
"""Tuxsitter background service (runs as root via systemd).

* tracks screen time of child accounts and logs them out when time is up
* keeps the nftables rules for the per-account DNS filter in place; the filter itself
  runs as a separate, unprivileged process (dnsproxy.py / tuxsitter-dns.service)
* answers the parent app and the child's panel indicator on a UNIX socket
"""
import grp
import json
import logging
import os
import pwd
import signal
import socket
import struct
import subprocess
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import admin  # noqa: E402
import browserpolicy  # noqa: E402
import common as C  # noqa: E402
import dnsfilter  # noqa: E402

log = logging.getLogger("tuxsitter")

TICK = 10           # seconds per loop
GRACE = 60          # seconds between "time is up" and logout
EXTRA_CHOICES = (15, 30, 60)
HISTORY_DAYS = 60


def today(t=None):
    return time.strftime("%Y-%m-%d", time.localtime(t))


def user_uid(name):
    try:
        return pwd.getpwnam(name).pw_uid
    except KeyError:
        return None


def is_admin(name):
    for g in ("sudo", "admin", "wheel"):
        try:
            gr = grp.getgrnam(g)
        except KeyError:
            continue
        if name in gr.gr_mem:
            return True
        try:
            if pwd.getpwnam(name).pw_gid == gr.gr_gid:
                return True
        except KeyError:
            pass
    return False


# ---------------------------------------------------------------------------
# Sessions (systemd-logind)
# ---------------------------------------------------------------------------
def sessions_by_user():
    """{user: {"any": bool, "active": bool}} - active = in front, not locked."""
    out = {}
    try:
        res = subprocess.run(["loginctl", "list-sessions", "--no-legend"],
                             capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return out
    for line in res.stdout.splitlines():
        parts = line.split()
        if len(parts) < 3:
            continue
        sid, user = parts[0], parts[2]
        try:
            info = subprocess.run(
                ["loginctl", "show-session", sid, "-p", "Active", "-p", "LockedHint",
                 "-p", "Class", "-p", "State"],
                capture_output=True, text=True, timeout=5).stdout
        except (OSError, subprocess.TimeoutExpired):
            continue
        p = dict(l.split("=", 1) for l in info.splitlines() if "=" in l)
        if p.get("Class", "user") != "user" or p.get("State") == "closing":
            continue
        e = out.setdefault(user, {"any": False, "active": False})
        e["any"] = True
        if p.get("Active") == "yes" and p.get("LockedHint") != "yes":
            e["active"] = True
    return out


def is_dns_proxy(pid):
    """True if the process belongs to tuxsitter-dns.service (checked via its cgroup,
    which only systemd can set)."""
    try:
        with open("/proc/%d/cgroup" % pid) as f:
            return any(line.rstrip().endswith("/tuxsitter-dns.service") for line in f)
    except (OSError, TypeError):
        return False


# ---------------------------------------------------------------------------
# Daemon
# ---------------------------------------------------------------------------
class Daemon:
    def __init__(self):
        self.lock = threading.RLock()
        self.cfg = C.load_config()
        self.cfg_mtime = self._mtime()
        self.state = C.load_json(C.STATE_FILE, {})
        for k in ("usage", "bonus", "override", "locked", "browser_policy"):
            self.state.setdefault(k, {})
        self.sessions = {}
        self.ending = {}         # user -> logout timestamp
        self.pin_fail = {}       # uid -> [count, locked_until]
        self.last_tick = time.time()
        self.last_save = 0
        self.last_nft_check = 0
        self.nft_ruleset = None
        self.dns_wanted = {}       # port -> (user, dns settings), served to the DNS filter process
        self.proxy_seen = 0        # last time the unprivileged DNS filter asked for its settings
        self.client_slots = threading.BoundedSemaphore(32)
        self.auth = admin.Authorizer()
        self.running = True

    # ---- configuration -----------------------------------------------------
    def _mtime(self):
        try:
            return os.stat(C.CONFIG_FILE).st_mtime
        except OSError:
            return 0

    def reload_if_changed(self, force=False):
        m = self._mtime()
        if force or m != self.cfg_mtime:
            self.cfg_mtime = m
            with self.lock:
                self.cfg = C.load_config()
            log.info("Configuration loaded (%d children)", len(self.cfg["children"]))
            self.apply_dns()
            self.apply_browser_policy()

    def apply_browser_policy(self):
        """Lock the browsers' own DNS setting (only while children are configured)."""
        want = bool(self.cfg.get("browser_doh_lock", True) and self.children())
        with self.lock:
            browserpolicy.apply(want, self.state["browser_policy"])
        self.save_state(force=True)

    def children(self):
        """[(user, cfg)] of enabled children whose account exists."""
        return [(u, c) for u, c in self.cfg["children"].items()
                if c.get("enabled", True) and user_uid(u) is not None]

    # ---- DNS ---------------------------------------------------------------
    def apply_dns(self):
        wanted, entries = {}, []
        for user, c in self.children():
            d = c["dns"]
            if not d.get("enabled"):
                continue
            uid = user_uid(user)
            port = C.DNS_PORT_BASE + uid
            if port > 65535:
                log.error("UID of %s too large for a DNS port", user)
                continue
            wanted[port] = (user, d)
            entries.append((uid, port, bool(d.get("block_bypass", True))))
        self.dns_wanted = wanted
        rules = dnsfilter.build_ruleset(entries) if entries else dnsfilter.EMPTY_RULESET
        self._nft(rules)
        self.nft_ruleset = rules

    def _nft(self, rules):
        try:
            r = subprocess.run(["nft", "-f", "-"], input=rules, capture_output=True,
                               text=True, timeout=10)
            if r.returncode != 0:
                # "delete table" fails if the table never existed -> retry without it
                alt = "\n".join(l for l in rules.splitlines() if not l.startswith("delete table"))
                r = subprocess.run(["nft", "-f", "-"], input=alt, capture_output=True,
                                   text=True, timeout=10)
                if r.returncode != 0:
                    log.error("nftables error: %s", r.stderr.strip())
        except (OSError, subprocess.TimeoutExpired) as e:
            log.error("cannot run nft: %s", e)

    def check_nft(self):
        """If something flushed the firewall, put our rules back."""
        if not self.nft_ruleset or self.nft_ruleset == dnsfilter.EMPTY_RULESET:
            return
        r = subprocess.run(["nft", "list", "table", "inet", dnsfilter.NFT_TABLE],
                           capture_output=True, text=True)
        if r.returncode != 0 or "redirect" not in r.stdout:
            log.warning("Firewall rules were missing - restoring them")
            self._nft(self.nft_ruleset)

    # ---- time accounting ---------------------------------------------------
    def status(self, user, now=None):
        now = now or time.time()
        c = self.cfg["children"].get(user)
        if not c or not c.get("enabled", True):
            return {"managed": False}
        d = today(now)
        rules, daytype = C.day_rules(c, now)
        used = self.state["usage"].get(user, {}).get(d, 0)
        bonus = self.state["bonus"].get(user, {}).get(d, 0)
        lt = time.localtime(now)
        sec_of_day = lt.tm_hour * 3600 + lt.tm_min * 60 + lt.tm_sec

        INF = 10 ** 9
        limit_total = rules["limit_min"] * 60 + bonus if rules["limit_enabled"] else None
        limit_left = (limit_total - used) if limit_total is not None else INF

        window_left = INF
        before_start = False
        if rules["window_enabled"]:
            start = C.hm_to_min(rules["start"]) * 60
            end = C.hm_to_min(rules["end"]) * 60
            if sec_of_day < start:
                window_left, before_start = 0, True
            elif sec_of_day >= end:
                window_left = 0
            else:
                window_left = end - sec_of_day
        ov = self.state["override"].get(user)
        if ov and ov.get("until", 0) > now:
            window_left = max(window_left, int(ov["until"] - now))

        remaining = max(0, min(limit_left, window_left))
        reason = None
        if self.state["locked"].get(user) == d:
            remaining, reason = 0, "locked"
        elif remaining <= 0:
            reason = "window" if window_left <= 0 else "limit"

        # when is the next screen time?
        if reason == "window" and before_start:
            next_day, next_time = "today", rules["start"]
        else:
            trules, _ = C.day_rules(c, now + 86400)
            next_day = "tomorrow"
            next_time = trules["start"] if trules["window_enabled"] else "00:00"

        # what is limiting today's remaining time: the daily limit or the end of the allowed hours?
        limited_by, until = None, None
        if reason is None and remaining < INF:
            if window_left <= limit_left:
                limited_by = "window"
                until = time.strftime("%H:%M", time.localtime(now + window_left))
            else:
                limited_by = "limit"

        ending = self.ending.get(user)
        return {
            "limited_by": limited_by,
            "until": until,
            "managed": True,
            "user": user,
            "daytype": daytype,
            "used": int(used),
            "limit": limit_total,
            "bonus": int(bonus),
            "remaining": int(remaining) if remaining < INF else None,
            "window": [rules["start"], rules["end"]] if rules["window_enabled"] else None,
            "reason": reason,
            "next_day": next_day,
            "next_time": next_time,
            "ending_in": max(0, int(ending - now)) if ending else None,
            "locked_today": self.state["locked"].get(user) == d,
            "holiday_mode": bool(c.get("holiday_mode")),
            "is_admin": is_admin(user),
            "online": bool(self.sessions.get(user, {}).get("any")),
            "history": self.history(user),
        }

    def history(self, user, days=14):
        u = self.state["usage"].get(user, {})
        return [[today(time.time() - i * 86400), int(u.get(today(time.time() - i * 86400), 0))]
                for i in range(days - 1, -1, -1)]

    # ---- main loop -----------------------------------------------------------
    def tick(self):
        now = time.time()
        dt = min(max(now - self.last_tick, 0), TICK * 2)   # cap after suspend
        self.last_tick = now
        self.reload_if_changed()
        self.sessions = sessions_by_user()
        d = today(now)
        with self.lock:
            for user, c in self.children():
                s = self.sessions.get(user)
                if s and s["active"]:
                    u = self.state["usage"].setdefault(user, {})
                    u[d] = u.get(d, 0) + dt
                st = self.status(user, now)
                if st["remaining"] is not None and st["remaining"] <= 0 and s and s["any"]:
                    if user not in self.ending:
                        self.ending[user] = now + GRACE
                        log.info("%s: time is up (%s) - logout in %ds", user, st["reason"], GRACE)
                    elif now >= self.ending[user]:
                        log.info("Logging out %s", user)
                        subprocess.run(["loginctl", "terminate-user", user], timeout=15)
                        self.ending.pop(user, None)
                else:
                    self.ending.pop(user, None)
        if now - self.last_nft_check > 60:
            self.last_nft_check = now
            self.check_nft()
        self.save_state()

    def save_state(self, force=False):
        now = time.time()
        if not force and now - self.last_save < 60:
            return
        self.last_save = now
        cutoff = today(now - HISTORY_DAYS * 86400)
        with self.lock:
            for k in ("usage", "bonus"):
                for user in list(self.state[k]):
                    self.state[k][user] = {d: v for d, v in self.state[k][user].items() if d >= cutoff}
            try:
                C.save_json(C.STATE_FILE, self.state, 0o600)
            except OSError as e:
                log.error("Cannot save state: %s", e)

    # ---- actions -----------------------------------------------------------
    def grant(self, user, minutes):
        """Extra time: adds to today's limit and opens the time window for that long."""
        now = time.time()
        d = today(now)
        with self.lock:
            b = self.state["bonus"].setdefault(user, {})
            c = self.cfg["children"].get(user)
            if c:
                # "+30 min" always means 30 minutes from now, even if the child is already over the limit
                rules, _ = C.day_rules(c, now)
                used = self.state["usage"].get(user, {}).get(d, 0)
                b[d] = max(b.get(d, 0), used - rules["limit_min"] * 60)
            b[d] = b.get(d, 0) + minutes * 60
            ov = self.state["override"].get(user, {})
            base = max(now, ov.get("until", 0))
            self.state["override"][user] = {"until": base + minutes * 60}
            if self.state["locked"].get(user) == d:
                self.state["locked"].pop(user)
            self.ending.pop(user, None)
        log.info("%s: +%d minutes", user, minutes)
        self.save_state(force=True)

    def set_locked(self, user, on):
        with self.lock:
            if on:
                self.state["locked"][user] = today()
                self.state["override"].pop(user, None)
            else:
                self.state["locked"].pop(user, None)
        self.save_state(force=True)

    def reset_today(self, user):
        d = today()
        with self.lock:
            for k in ("usage", "bonus"):
                self.state[k].get(user, {}).pop(d, None)
            self.state["override"].pop(user, None)
            self.state["locked"].pop(user, None)
        self.save_state(force=True)

    # ---- socket ------------------------------------------------------------
    def serve(self):
        os.makedirs(C.RUN_DIR, exist_ok=True)
        os.chmod(C.RUN_DIR, 0o755)
        try:
            os.unlink(C.SOCKET_PATH)
        except OSError:
            pass
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        srv.bind(C.SOCKET_PATH)
        os.chmod(C.SOCKET_PATH, 0o666)
        srv.listen(16)
        while self.running:
            try:
                conn, _ = srv.accept()
            except OSError:
                continue
            if not self.client_slots.acquire(blocking=False):
                conn.close()          # too many open connections (e.g. a flood) -> refuse
                continue
            threading.Thread(target=self._client, args=(conn,), daemon=True).start()

    def _client(self, conn):
        conn.settimeout(5)
        try:
            cred = conn.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
            pid, uid, _ = struct.unpack("3i", cred)
            buf = b""
            while not buf.endswith(b"\n") and len(buf) < 65536:
                chunk = conn.recv(4096)
                if not chunk:
                    break
                buf += chunk
            msg = json.loads(buf.decode() or "{}")
            resp = self.handle(uid, msg, pid)
        except Exception as e:  # noqa: BLE001
            log.debug("client error: %s", e)
            resp = {"ok": False, "error": "internal"}
        try:
            conn.sendall(json.dumps(resp).encode() + b"\n")
        except OSError:
            pass
        conn.close()
        self.client_slots.release()

    def handle(self, uid, msg, pid=None):
        cmd = msg.get("cmd")
        try:
            me = pwd.getpwuid(uid).pw_name
        except KeyError:
            me = None

        if cmd == "status":
            with self.lock:
                st = self.status(me) if me else {"managed": False}
            st.pop("history", None)
            st.pop("is_admin", None)
            return st

        if cmd == "extra" and uid != 0:
            try:
                minutes = int(msg.get("minutes", 0))
            except (TypeError, ValueError):
                minutes = 0
            if minutes not in EXTRA_CHOICES or me not in self.cfg["children"]:
                return {"ok": False, "error": "invalid"}
            f = self.pin_fail.get(uid, [0, 0])
            if f[1] > time.time():
                return {"ok": False, "error": "locked_out", "retry_in": int(f[1] - time.time())}
            if not self.cfg.get("pin"):
                return {"ok": False, "error": "no_pin"}
            if not C.check_pin(self.cfg["pin"], str(msg.get("pin", ""))):
                f[0] += 1
                if f[0] >= 3:
                    f[1] = time.time() + min(3600, 60 * 2 ** (f[0] - 3))
                self.pin_fail[uid] = f
                log.warning("%s: wrong parent PIN (%d)", me, f[0])
                return {"ok": False, "error": "wrong_pin"}
            self.pin_fail.pop(uid, None)
            self.grant(me, minutes)
            return {"ok": True}

        if cmd == "dns_config":
            # only for the unprivileged DNS filter process started by systemd
            if uid != 0 and not is_dns_proxy(pid):
                return {"ok": False, "error": "forbidden"}
            self.proxy_seen = time.time()
            with self.lock:
                return {"ok": True, "children": {str(port): [u, d] for port, (u, d) in self.dns_wanted.items()}}

        # ---- everything below: parent app (root, or authorized via polkit) ----
        try:
            return self.handle_admin(uid, me, msg, pid)
        except admin.AdminError as e:
            return {"ok": False, "error": str(e)}

    def handle_admin(self, uid, me, msg, pid):
        cmd = msg.get("cmd")
        if cmd == "auth":
            self.auth.authorize(pid, uid)         # shows the desktop's password dialog
            return {"ok": True}
        if not self.auth.is_authorized(pid, uid):
            return {"ok": False, "error": "auth_required"}

        user = msg.get("user")
        if cmd == "status_all":
            with self.lock:
                return {u: self.status(u) for u in self.cfg["children"]}
        if cmd == "get_config":
            with self.lock:
                return {"ok": True, "config": admin.public_config(self.cfg), "me": me,
                        "users": admin.candidate_users(exclude=me)}
        if cmd == "set_config":
            with self.lock:
                cfg = admin.apply_settings(C.load_config(), msg.get("config"))
                C.save_config(cfg)
            self.reload_if_changed(force=True)
            return {"ok": True}
        if cmd == "set_pin":
            pin = str(msg.get("pin", ""))
            if not (pin.isdigit() and 4 <= len(pin) <= 8):
                raise admin.AdminError("invalid")
            with self.lock:
                cfg = C.load_config()
                cfg["pin"] = C.hash_pin(pin)
                C.save_config(cfg)
            self.reload_if_changed(force=True)
            log.info("Parent PIN changed")
            return {"ok": True}
        if cmd in ("add_child", "create_account"):
            if cmd == "create_account":
                admin.create_account(user, msg.get("name"), msg.get("password"))
            valid = {u["user"] for u in admin.candidate_users(exclude=me)}
            if user not in valid:
                raise admin.AdminError("invalid")
            with self.lock:
                cfg = C.load_config()
                cfg["children"].setdefault(user, C.new_child_config())
                C.save_config(cfg)
            self.reload_if_changed(force=True)
            log.info("Child added: %s", user)
            return {"ok": True}
        if cmd == "reload":
            self.reload_if_changed(force=True)
            return {"ok": True}
        if cmd == "health":
            age = time.time() - self.proxy_seen if self.proxy_seen else None
            return {"ok": True, "dns_needed": bool(self.dns_wanted), "dns_proxy_age": age}
        if cmd == "dnstest":
            return admin.dns_test(msg.get("dns", {}))

        # ---- commands for one child ----
        if user not in self.cfg["children"]:
            return {"ok": False, "error": "unknown_child"}
        if cmd == "remove_child":
            with self.lock:
                cfg = C.load_config()
                cfg["children"].pop(user, None)
                C.save_config(cfg)
            self.reload_if_changed(force=True)
            log.info("Child removed: %s", user)
            return {"ok": True}
        if cmd == "remove_admin":
            if user == me:
                raise admin.AdminError("forbidden")
            admin.remove_admin_rights(user)
            return {"ok": True}
        if cmd == "grant":
            try:
                minutes = int(msg.get("minutes", 15))
            except (TypeError, ValueError):
                raise admin.AdminError("invalid")
            self.grant(user, max(1, min(minutes, 600)))
            return {"ok": True}
        if cmd == "lock":
            self.set_locked(user, bool(msg.get("on", True)))
            return {"ok": True}
        if cmd == "reset_today":
            self.reset_today(user)
            return {"ok": True}
        return {"ok": False, "error": "unknown_cmd"}

    # ---- start -------------------------------------------------------------
    def run(self):
        os.makedirs(C.CONFIG_DIR, mode=0o700, exist_ok=True)
        os.makedirs(C.STATE_DIR, mode=0o700, exist_ok=True)
        if not os.path.exists(C.CONFIG_FILE):
            C.save_config(C.load_config())
        self.reload_if_changed(force=True)
        threading.Thread(target=self.serve, daemon=True).start()
        log.info("Tuxsitter %s started", C.VERSION)
        while self.running:
            try:
                self.tick()
            except Exception:  # noqa: BLE001
                log.exception("error in main loop")
            for _ in range(TICK * 2):
                if not self.running:
                    break
                time.sleep(0.5)
        self.save_state(force=True)
        log.info("stopped")


def cleanup():
    """On uninstall: remove our firewall table and browser policies."""
    subprocess.run(["nft", "delete", "table", "inet", dnsfilter.NFT_TABLE], capture_output=True)
    state = C.load_json(C.STATE_FILE, {})
    browserpolicy.disable(state.get("browser_policy", {}))
    state["browser_policy"] = {}
    try:
        C.save_json(C.STATE_FILE, state, 0o600)
    except OSError:
        pass


def main():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    if os.geteuid() != 0:
        print("Tuxsitter daemon must run as root.", file=sys.stderr)
        sys.exit(1)
    if "--cleanup" in sys.argv:
        cleanup()
        return
    d = Daemon()

    def stop(*_):
        d.running = False
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    d.run()


if __name__ == "__main__":
    main()
