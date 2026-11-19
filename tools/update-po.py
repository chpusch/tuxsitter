#!/usr/bin/env python3
"""Extract translatable strings and update po/*.po.

Finds every  _("...")  and  N_("...")  call in src/tuxsitter/*.py,
writes po/tuxsitter.pot and adds new strings (empty) to each po/<lang>.po.
Existing translations are kept; strings no longer used are dropped.

    python3 tools/update-po.py
"""
import ast
import glob
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
from pofile import read_po, write_po  # noqa: E402


def extract():
    found = {}
    for path in sorted(glob.glob(os.path.join(ROOT, "src", "tuxsitter", "*.py"))):
        tree = ast.parse(open(path, encoding="utf-8").read(), path)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not node.args:
                continue
            f = node.func
            name = f.id if isinstance(f, ast.Name) else f.attr if isinstance(f, ast.Attribute) else None
            if name not in ("_", "N_"):
                continue
            a = node.args[0]
            if isinstance(a, ast.Constant) and isinstance(a.value, str):
                ref = "%s:%d" % (os.path.relpath(path, ROOT), node.lineno)
                found.setdefault(a.value, []).append(ref)
    return found


def main():
    msgs = extract()
    pot = [(refs, mid, "") for mid, refs in msgs.items()]
    write_po(os.path.join(ROOT, "po", "tuxsitter.pot"), pot, lang=None)
    for po in sorted(glob.glob(os.path.join(ROOT, "po", "*.po"))):
        lang = os.path.splitext(os.path.basename(po))[0]
        old = read_po(po)
        entries = [(refs, mid, old.get(mid, "")) for mid, refs in msgs.items()]
        write_po(po, entries, lang=lang)
        missing = sum(1 for _, _, s in entries if not s)
        print("%s: %d strings, %d untranslated" % (lang, len(entries), missing))
    print("pot: %d strings" % len(pot))


if __name__ == "__main__":
    main()
