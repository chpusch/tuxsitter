"""Translations (gettext). English is the source language."""
import gettext
import os

DOMAIN = "tuxsitter"
_HERE = os.path.dirname(os.path.abspath(__file__))
_CANDIDATES = [
    os.environ.get("TUXSITTER_LOCALEDIR", ""),
    os.path.join(_HERE, "..", "..", "build", "locale"),   # running from the git checkout
    "/usr/share/locale",
]

_t = gettext.NullTranslations()


def setup(lang=None):
    """Load the catalogue. `lang` overrides the environment (e.g. "de_DE.UTF-8")."""
    global _t
    if lang:
        os.environ["LANGUAGE"] = lang.split(".")[0]
    for d in _CANDIDATES:
        if d and os.path.isdir(d):
            t = gettext.translation(DOMAIN, localedir=d, fallback=True)
            if type(t) is not gettext.NullTranslations:  # found a real catalogue
                _t = t
                return
    _t = gettext.NullTranslations()


def _(msg):
    return _t.gettext(msg)


def N_(msg):
    """Mark a string for translation without translating it yet."""
    return msg


setup()
