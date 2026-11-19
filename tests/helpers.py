import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_tmp = tempfile.mkdtemp(prefix="tuxsitter-test-")
for k, sub in (("TUXSITTER_CONFIG_DIR", "etc"), ("TUXSITTER_STATE_DIR", "var"), ("TUXSITTER_RUN_DIR", "run")):
    os.environ.setdefault(k, os.path.join(_tmp, sub))
sys.path.insert(0, os.path.join(ROOT, "src", "tuxsitter"))
sys.path.insert(0, os.path.join(ROOT, "tools"))
