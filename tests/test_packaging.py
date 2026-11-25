import os
import re
import unittest
import xml.etree.ElementTree as ET

from tests import helpers

CONTROL = os.path.join(helpers.ROOT, "packaging", "deb", "control")
METAINFO = os.path.join(helpers.ROOT, "data", "io.github.chpusch.tuxsitter.metainfo.xml")
COMMON = os.path.join(helpers.ROOT, "src", "tuxsitter", "common.py")


def read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


class PackagingTests(unittest.TestCase):
    def test_cairo_dependency(self):
        """Gtk 'draw' handlers get a cairo.Context; without python3-gi-cairo PyGObject fails with
        "Couldn't find foreign struct converter for 'cairo.Context'" (not installed by default on Ubuntu)."""
        gui = read(os.path.join(helpers.ROOT, "src", "tuxsitter", "gui.py"))
        if '"draw"' in gui:
            depends = re.search(r"^Depends:(.*)$", read(CONTROL), re.M).group(1)
            self.assertIn("python3-gi-cairo", depends)

    def test_metainfo_lists_current_version(self):
        version = re.search(r'^VERSION = "([^"]+)"', read(COMMON), re.M).group(1)
        root = ET.parse(METAINFO).getroot()
        releases = root.findall("./releases/release")
        self.assertTrue(releases)
        self.assertEqual(releases[0].get("version"), version,
                         "add a <release> entry for %s to the metainfo file" % version)
        self.assertEqual(root.findtext("project_license"), "GPL-3.0-or-later")
        self.assertEqual(root.findtext("launchable"), "tuxsitter.desktop")


if __name__ == "__main__":
    unittest.main()
