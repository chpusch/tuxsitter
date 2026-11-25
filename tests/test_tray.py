import ast
import os
import unittest

from tests import helpers


def load_wants_xapp():
    """tray.py needs GTK on import, so only its pure helper is loaded here."""
    src = open(os.path.join(helpers.ROOT, "src", "tuxsitter", "tray.py"), encoding="utf-8").read()
    tree = ast.parse(src)
    keep = [n for n in tree.body
            if (isinstance(n, ast.FunctionDef) and n.name == "wants_xapp")
            or (isinstance(n, ast.Assign) and any(getattr(t, "id", "") == "XAPP_DESKTOPS" for t in n.targets))]
    ns = {}
    exec(compile(ast.Module(body=keep, type_ignores=[]), "tray.py", "exec"), ns)
    return ns["wants_xapp"]


class PanelIconTests(unittest.TestCase):
    def test_xapp_only_where_an_xapp_applet_exists(self):
        wants_xapp = load_wants_xapp()
        for desktop in ("X-Cinnamon", "Cinnamon", "MATE", "XFCE"):
            self.assertTrue(wants_xapp(desktop), desktop)
        # Ubuntu (GNOME) shows AppIndicators, not XApp icons
        for desktop in ("ubuntu:GNOME", "GNOME", "KDE", "", None):
            self.assertFalse(wants_xapp(desktop), desktop)


if __name__ == "__main__":
    unittest.main()
