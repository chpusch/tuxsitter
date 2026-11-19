import glob
import os
import re
import unittest

from tests import helpers
from pofile import read_po


class TranslationTests(unittest.TestCase):
    def test_complete_and_placeholders(self):
        pot = read_po(os.path.join(helpers.ROOT, "po", "tuxsitter.pot"))
        self.assertTrue(pot)
        for po in glob.glob(os.path.join(helpers.ROOT, "po", "*.po")):
            cat = read_po(po)
            for mid in pot:
                with self.subTest(po=os.path.basename(po), msgid=mid):
                    self.assertTrue(cat.get(mid), "untranslated")
                    self.assertEqual(set(re.findall(r"\{\w+\}", mid)), set(re.findall(r"\{\w+\}", cat[mid])))


if __name__ == "__main__":
    unittest.main()
