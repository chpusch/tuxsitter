#!/usr/bin/env python3
"""Compile po/<lang>.po into build/locale/<lang>/LC_MESSAGES/tuxsitter.mo"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
from pofile import compile_mo  # noqa: E402

out_root = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "build", "locale")
for lang in open(os.path.join(ROOT, "po", "LINGUAS")).read().split():
    d = os.path.join(out_root, lang, "LC_MESSAGES")
    os.makedirs(d, exist_ok=True)
    n = compile_mo(os.path.join(ROOT, "po", lang + ".po"), os.path.join(d, "tuxsitter.mo"), lang)
    print("%s: %d messages" % (lang, n))
