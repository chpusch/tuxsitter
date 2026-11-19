import threading
import time
import unittest

from tests import helpers  # noqa: F401
import common as C
import daemon as Dm


def make_daemon(child):
    d = Dm.Daemon.__new__(Dm.Daemon)
    d.lock = threading.RLock()
    d.cfg = C.load_config()
    d.cfg["children"] = {"kid": child}
    d.state = {"usage": {}, "bonus": {}, "override": {}, "locked": {}}
    d.sessions, d.ending, d.pin_fail = {}, {}, {}
    d.save_state = lambda force=False: None
    return d


def at(hour, minute=0, weekday=2):
    """Timestamp for this week's <weekday> (0=Mon) at hour:minute local time."""
    lt = time.localtime()
    base = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, hour, minute, 0, 0, 0, -1))
    return base + (weekday - lt.tm_wday) * 86400


class StatusTests(unittest.TestCase):
    def test_limit_and_window(self):
        c = C.new_child_config()
        d = make_daemon(c)
        now = at(15)
        d.state["usage"]["kid"] = {Dm.today(now): 60 * 60}
        st = d.status("kid", now)
        self.assertEqual(st["daytype"], "school")
        self.assertEqual(st["remaining"], 30 * 60)          # 90 min limit - 60 used
        self.assertEqual(st["limited_by"], "limit")
        st = d.status("kid", at(19, 44))                     # 16 min before 20:00
        self.assertEqual((st["remaining"], st["limited_by"], st["until"]), (16 * 60, "window", "20:00"))
        st = d.status("kid", at(21))
        self.assertEqual((st["remaining"], st["reason"]), (0, "window"))
        st = d.status("kid", at(6))
        self.assertEqual((st["next_day"], st["next_time"]), ("today", "07:00"))

    def test_weekend_and_holiday(self):
        c = C.new_child_config()
        d = make_daemon(c)
        self.assertEqual(d.status("kid", at(12, weekday=5))["daytype"], "weekend")
        c["holiday_mode"] = True
        self.assertEqual(d.status("kid", at(12, weekday=1))["daytype"], "weekend")

    def test_grant_opens_window_and_limit(self):
        c = C.new_child_config()
        d = make_daemon(c)
        d.state["usage"]["kid"] = {Dm.today(): 999 * 60}
        d.grant("kid", 30)
        st = d.status("kid")
        self.assertGreater(st["remaining"], 0)

    def test_lock(self):
        d = make_daemon(C.new_child_config())
        d.set_locked("kid", True)
        st = d.status("kid")
        self.assertEqual((st["remaining"], st["reason"]), (0, "locked"))

    def test_pin_and_rate_limit(self):
        d = make_daemon(C.new_child_config())
        d.cfg["pin"] = C.hash_pin("4711")
        d.handle = Dm.Daemon.handle.__get__(d)
        import pwd
        uid = 65534  # nobody
        name = pwd.getpwuid(uid).pw_name
        d.cfg["children"] = {name: C.new_child_config()}
        for _ in range(3):
            self.assertEqual(d.handle(uid, {"cmd": "extra", "pin": "0000", "minutes": 30})["error"], "wrong_pin")
        self.assertEqual(d.handle(uid, {"cmd": "extra", "pin": "4711", "minutes": 30})["error"], "locked_out")
        self.assertEqual(d.handle(uid, {"cmd": "grant", "user": name})["error"], "forbidden")


class FormatTests(unittest.TestCase):
    def test_duration(self):
        self.assertEqual(C.fmt_duration(3900, short=True), "1:05")
        self.assertEqual(C.hm_to_min("07:30"), 450)
        self.assertEqual(C.min_to_hm(1440), "24:00")


if __name__ == "__main__":
    unittest.main()
