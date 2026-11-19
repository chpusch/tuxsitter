import ast
import glob
import os
import unittest

from tests import helpers


class GettextShadowingTests(unittest.TestCase):
    """A parameter called `_` (e.g. `def cb(self, *_)`) hides the gettext function `_()`
    inside that function, so every translated string there raises TypeError at runtime."""

    def test_no_underscore_params_calling_gettext(self):
        problems = []
        for path in glob.glob(os.path.join(helpers.ROOT, "src", "tuxsitter", "*.py")):
            tree = ast.parse(open(path, encoding="utf-8").read())
            for fn in ast.walk(tree):
                if not isinstance(fn, (ast.FunctionDef, ast.Lambda)):
                    continue
                a = fn.args
                names = [x.arg for x in a.args + a.kwonlyargs]
                names += [a.vararg.arg] if a.vararg else []
                names += [a.kwarg.arg] if a.kwarg else []
                # assigning to `_` anywhere in the function (loops, unpacking) has the same effect
                names += [n.id for n in ast.walk(fn) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store)]
                if "_" not in names:
                    continue
                for node in ast.walk(fn):
                    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "_":
                        problems.append("%s:%d" % (os.path.basename(path), fn.lineno))
                        break
        self.assertEqual(problems, [], "functions with a `_` parameter that call _(): %s" % problems)


if __name__ == "__main__":
    unittest.main()
