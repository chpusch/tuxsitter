import unittest

from tests import helpers  # noqa: F401
import common as C
import updates as U


class UpdateTests(unittest.TestCase):
    def test_parse_version(self):
        self.assertEqual(U.parse_version("v0.10.2"), (0, 10, 2))
        self.assertGreater(U.parse_version("0.10.0"), U.parse_version("v0.9.9"))
        self.assertEqual(U.parse_version("nightly"), ())

    def test_newest_release(self):
        releases = [
            {"tag_name": "v0.3.0", "draft": True, "html_url": "x"},
            {"tag_name": "v0.2.1", "prerelease": True, "html_url": "https://e/0.2.1"},
            {"tag_name": "v0.10.0", "html_url": "https://e/0.10.0"},
            {"tag_name": "junk"},
        ]
        self.assertEqual(U.newest_release(releases), ("0.10.0", "https://e/0.10.0"))
        self.assertEqual(U.newest_release([]), (None, None))

    def test_check_uses_cache(self):
        C.save_json(U.CACHE_FILE, {"checked": 9e12, "latest": "99.0", "url": "https://e"}, 0o600)
        self.assertEqual(U.check(), ("99.0", "https://e"))
        C.save_json(U.CACHE_FILE, {"checked": 9e12, "latest": C.VERSION, "url": "https://e"}, 0o600)
        self.assertIsNone(U.check())


if __name__ == "__main__":
    unittest.main()
