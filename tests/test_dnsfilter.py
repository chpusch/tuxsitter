import struct
import threading
import unittest

from tests import helpers  # noqa: F401
import dnsfilter as D


class FakeUpstream:
    def __init__(self):
        self.calls = 0

    def query(self, data, tcp=False):
        self.calls += 1
        qtype = D.parse_question(data)[3]
        answers = [(D.T_A, 120, bytes([1, 2, 3, 4]))] if qtype == D.T_A else []
        return D.build_response(data, 0, answers)


def make_filter(**cfg):
    f = D.ChildFilter.__new__(D.ChildFilter)
    f.user = "kid"
    f.cfg = dict({"safesearch": True, "youtube": "moderate", "block_bypass": True}, **cfg)
    f.upstream = FakeUpstream()
    f.cache = {}
    f.lock = threading.Lock()
    f.stats = {"queries": 0, "blocked": 0, "errors": 0}
    return f


class PacketTests(unittest.TestCase):
    def test_roundtrip(self):
        q = D.make_query("www.example.com", D.T_A, 0xBEEF)
        qid, _, name, qtype, qclass, _ = D.parse_question(q)
        self.assertEqual((qid, name, qtype, qclass), (0xBEEF, "www.example.com", D.T_A, 1))
        r = D.build_response(q, D.RC_NOERROR, [(D.T_A, 60, b"\x0a\x00\x00\x01")])
        rc, recs = D.parse_records(r)
        self.assertEqual(rc, 0)
        self.assertEqual(recs[0][0], "www.example.com")
        self.assertEqual(struct.unpack("!H", r[:2])[0], 0xBEEF)


class PolicyTests(unittest.TestCase):
    def test_safesearch_targets(self):
        cfg = {"safesearch": True, "youtube": "strict"}
        self.assertEqual(D.safesearch_target("www.google.de", cfg), "forcesafesearch.google.com")
        self.assertEqual(D.safesearch_target("www.bing.com", cfg), "strict.bing.com")
        self.assertEqual(D.safesearch_target("www.youtube.com", cfg), "restrict.youtube.com")
        self.assertIsNone(D.safesearch_target("www.wikipedia.org", cfg))
        self.assertIsNone(D.safesearch_target("www.google.de", {"safesearch": False, "youtube": "off"}))

    def test_blocking(self):
        on = {"block_bypass": True}
        self.assertTrue(D.is_blocked("use-application-dns.net", {"block_bypass": False}))
        self.assertTrue(D.is_blocked("mozilla.cloudflare-dns.com", on))
        self.assertTrue(D.is_blocked("dns.google", on))
        self.assertFalse(D.is_blocked("dns.google", {"block_bypass": False}))
        self.assertFalse(D.is_blocked("example.com", on))


class FilterTests(unittest.TestCase):
    def test_handle(self):
        f = make_filter()
        r = f.handle(D.make_query("dns.google", D.T_A, 1))
        self.assertEqual(D.parse_records(r)[0], D.RC_NXDOMAIN)
        r = f.handle(D.make_query("www.google.com", D.T_A, 2))
        rc, recs = D.parse_records(r)
        self.assertEqual(recs[0][0], "www.google.com")
        r = f.handle(D.make_query("www.google.com", 65, 3))   # HTTPS record -> empty
        self.assertEqual(D.parse_records(r), (0, []))
        f.handle(D.make_query("example.com", D.T_A, 4))
        f.handle(D.make_query("example.com", D.T_A, 5))
        self.assertEqual(f.upstream.calls, 2)   # second example.com came from the cache

    def test_bad_config_fails_closed(self):
        with self.assertRaises(ValueError):
            D.make_upstream({"provider": "nextdns", "nextdns_id": "bad id!"})
        with self.assertRaises(ValueError):
            D.make_upstream({"provider": "custom", "custom": "not-an-ip"})
        ff = D._FailFilter("kid", {})
        r = ff.handle(D.make_query("example.com", D.T_A, 9))
        self.assertEqual(D.parse_records(r)[0], D.RC_SERVFAIL)


class RulesetTests(unittest.TestCase):
    def test_ruleset(self):
        rs = D.build_ruleset([(1001, 21001, True), (1002, 21002, False)])
        self.assertIn("meta skuid 1001 udp dport 53 redirect to :21001", rs)
        self.assertIn("meta skuid 1001 ip daddr @doh4 reject", rs)
        self.assertNotIn("meta skuid 1002 ip daddr", rs)
        self.assertIn("table inet tuxsitter", rs)


if __name__ == "__main__":
    unittest.main()
