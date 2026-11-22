import os
import stat
import tempfile
import threading
import unittest

from tests import helpers  # noqa: F401
import admin
import common as C
import daemon as Dm


def fake_pkcheck(exit_code):
    d = tempfile.mkdtemp()
    p = os.path.join(d, "pkcheck")
    with open(p, "w") as f:
        f.write("#!/bin/sh\necho \"$@\" > %s/args\nexit %d\n" % (d, exit_code))
    os.chmod(p, os.stat(p).st_mode | stat.S_IEXEC)
    return p, d


def make_daemon(pkcheck_exit=0):
    d = Dm.Daemon.__new__(Dm.Daemon)
    d.lock = threading.RLock()
    C.save_config({"version": 1, "pin": None, "update_check": True,
                   "children": {"kid": C.new_child_config()}})
    d.cfg = C.load_config()
    d.state = {"usage": {}, "bonus": {}, "override": {}, "locked": {}}
    d.sessions, d.ending, d.pin_fail = {}, {}, {}
    d.save_state = lambda force=False: None
    d.reload_if_changed = lambda force=False: setattr(d, "cfg", C.load_config())
    d.auth = admin.Authorizer(fake_pkcheck(pkcheck_exit)[0])
    return d


ME = os.getpid()
UID = 4242   # a non-root user id


class AuthorizationTests(unittest.TestCase):
    def test_unauthorized_parent_commands_are_refused(self):
        d = make_daemon()
        for cmd in ("get_config", "set_config", "set_pin", "status_all", "remove_admin",
                    "create_account", "grant", "lock"):
            r = d.handle(UID, {"cmd": cmd, "user": "kid"}, ME)
            self.assertEqual(r.get("error"), "auth_required", cmd)

    def test_polkit_yes_then_session_is_remembered(self):
        d = make_daemon(0)
        self.assertTrue(d.handle(UID, {"cmd": "auth"}, ME)["ok"])
        r = d.handle(UID, {"cmd": "get_config"}, ME)
        self.assertTrue(r["ok"])
        self.assertNotIn("pin", r["config"])            # the PIN hash never leaves the service
        self.assertIn("pin_set", r["config"])
        # another process of the same user is NOT authorized
        self.assertEqual(d.handle(UID, {"cmd": "get_config"}, 1)["error"], "auth_required")
        # a different user with the same pid is not either
        self.assertEqual(d.handle(UID + 1, {"cmd": "get_config"}, ME)["error"], "auth_required")

    def test_polkit_passes_process_identity(self):
        path, folder = fake_pkcheck(0)
        a = admin.Authorizer(path)
        a.authorize(ME, UID)
        args = open(os.path.join(folder, "args")).read()
        self.assertIn("--action-id io.github.chpusch.tuxsitter.manage", args)
        self.assertIn("--process %d,%d,%d" % (ME, admin.process_start_time(ME), UID), args)

    def test_wrong_password_locks_out_after_three_attempts(self):
        d = make_daemon(1)
        for _i in range(3):
            self.assertEqual(d.handle(UID, {"cmd": "auth"}, ME)["error"], "auth_failed")
        self.assertEqual(d.handle(UID, {"cmd": "auth"}, ME)["error"], "locked_out")

    def test_root_needs_no_polkit(self):
        d = make_daemon(1)
        self.assertTrue(d.handle(0, {"cmd": "get_config"}, None)["ok"])


class SettingsTests(unittest.TestCase):
    def setUp(self):
        self.d = make_daemon(0)
        self.d.handle(UID, {"cmd": "auth"}, ME)

    def test_set_config_and_pin(self):
        cfg = self.d.handle(UID, {"cmd": "get_config"}, ME)["config"]
        cfg["children"]["kid"]["school"]["limit_min"] = 45
        cfg["children"]["kid"]["dns"]["provider"] = "adguard"
        self.assertTrue(self.d.handle(UID, {"cmd": "set_config", "config": cfg}, ME)["ok"])
        saved = C.load_config()
        self.assertEqual(saved["children"]["kid"]["school"]["limit_min"], 45)
        self.assertEqual(saved["children"]["kid"]["dns"]["provider"], "adguard")
        self.assertTrue(self.d.handle(UID, {"cmd": "set_pin", "pin": "1357"}, ME)["ok"])
        self.assertTrue(C.check_pin(C.load_config()["pin"], "1357"))
        self.assertEqual(self.d.handle(UID, {"cmd": "set_pin", "pin": "12"}, ME)["error"], "invalid")

    def test_invalid_settings_are_rejected(self):
        bad = [
            {"school": {"limit_min": "lots"}},
            {"school": {"start": "25:00"}},
            {"dns": {"provider": "evil"}},
            {"dns": {"custom": "1.2.3.4; rm -rf /"}},
            {"dns": {"nextdns_id": "../../etc"}},
            {"enabled": "yes"},
        ]
        for b in bad:
            r = self.d.handle(UID, {"cmd": "set_config", "config": {"children": {"kid": b}}}, ME)
            self.assertFalse(r.get("ok"), b)
        # children cannot be added through set_config
        r = self.d.handle(UID, {"cmd": "set_config", "config": {"children": {"root": {}}}}, ME)
        self.assertEqual(r["error"], "unknown_child")

    def test_account_names_are_validated(self):
        for name in ("Root", "a", "x;rm", "-rf", "../etc"):
            r = self.d.handle(UID, {"cmd": "create_account", "user": name, "name": "X", "password": "secret"}, ME)
            self.assertEqual(r.get("error"), "bad_username", name)


if __name__ == "__main__":
    unittest.main()


class DnsProxyEndpointTests(unittest.TestCase):
    def test_only_the_dns_service_gets_the_filter_settings(self):
        d = make_daemon()
        d.dns_wanted = {21001: ("kid", {"provider": "nextdns", "nextdns_id": "abc123"})}
        d.proxy_seen = 0
        # an ordinary process (here: the test itself) is not tuxsitter-dns.service
        self.assertEqual(d.handle(UID, {"cmd": "dns_config"}, ME)["error"], "forbidden")
        r = d.handle(0, {"cmd": "dns_config"}, ME)
        self.assertEqual(r["children"], {"21001": ["kid", {"provider": "nextdns", "nextdns_id": "abc123"}]})
        self.assertGreater(d.proxy_seen, 0)

    def test_cgroup_check(self):
        self.assertFalse(Dm.is_dns_proxy(ME))
        self.assertFalse(Dm.is_dns_proxy(None))


class BrowserPolicyTests(unittest.TestCase):
    def test_enable_merge_and_restore(self):
        import json
        import browserpolicy as B
        root = tempfile.mkdtemp()
        old = (B.FIREFOX, B.CHROMIUM_DIRS)
        try:
            B.FIREFOX = os.path.join(root, "firefox", "policies.json")
            B.CHROMIUM_DIRS = [os.path.join(root, "chrome")]
            os.makedirs(os.path.dirname(B.FIREFOX))
            original = json.dumps({"policies": {"DisableTelemetry": True}})
            with open(B.FIREFOX, "w") as f:
                f.write(original)
            state = {}
            B.apply(True, state)
            pol = json.load(open(B.FIREFOX))["policies"]
            self.assertEqual(pol["DNSOverHTTPS"], {"Enabled": False, "Locked": True})
            self.assertTrue(pol["DisableTelemetry"])                      # existing policy kept
            self.assertEqual(json.load(open(os.path.join(root, "chrome", "tuxsitter.json"))),
                             {"DnsOverHttpsMode": "off"})
            B.apply(True, state)                                          # idempotent
            B.apply(False, state)
            self.assertEqual(open(B.FIREFOX).read(), original)            # restored exactly
            self.assertFalse(os.path.exists(os.path.join(root, "chrome", "tuxsitter.json")))
        finally:
            B.FIREFOX, B.CHROMIUM_DIRS = old


class MigrationTests(unittest.TestCase):
    def kid(self, provider):
        c = C.new_child_config()
        c["dns"]["provider"] = provider
        return c

    def test_cloudflare_moves_to_opendns_once(self):
        C.save_config({"version": 1, "pin": None, "children": {
            "a": self.kid("cloudflare"), "b": self.kid("cleanbrowsing"), "c": self.kid("nextdns")}})
        cfg = C.load_config()
        self.assertEqual(cfg["version"], C.CONFIG_VERSION)
        self.assertEqual([cfg["children"][u]["dns"]["provider"] for u in "abc"],
                         ["opendns", "cleanbrowsing", "nextdns"])
        # persisted, and a deliberate choice of Cloudflare afterwards is kept
        self.assertEqual(C.load_json(C.CONFIG_FILE, {})["version"], C.CONFIG_VERSION)
        cfg["children"]["a"]["dns"]["provider"] = "cloudflare"
        C.save_config(cfg)
        self.assertEqual(C.load_config()["children"]["a"]["dns"]["provider"], "cloudflare")

    def test_config_without_version_is_migrated(self):
        C.save_config({"pin": None, "children": {"a": self.kid("cloudflare")}})
        self.assertEqual(C.load_config()["children"]["a"]["dns"]["provider"], "opendns")

    def test_new_child_defaults_to_opendns(self):
        self.assertEqual(C.new_child_config()["dns"]["provider"], "opendns")
