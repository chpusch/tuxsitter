# Contributing to Tuxsitter

Thanks for helping! Bug reports, ideas, translations and code are all welcome.

## Reporting bugs

Please open an [issue](https://github.com/chpusch/tuxsitter/issues) and include:

- your distribution and desktop (e.g. Linux Mint 22 Cinnamon)
- what you did, what you expected, what happened
- the service log: `journalctl -u tuxsitter --since "1 hour ago"`

## Translations

All texts live in `po/`. English is the source language.

**Improve an existing language:** edit `po/<lang>.po` and open a pull request.

**Add a new language** (example: Italian, `it`):

```sh
cp po/tuxsitter.pot po/it.po
# translate every msgstr "" in po/it.po (Poedit works great)
echo it >> po/LINGUAS
python3 tools/update-po.py     # checks that nothing is missing
python3 -m unittest discover -s tests -t .
```

Tips:
- Keep placeholders like `{time}`, `{name}` or `{h}` exactly as they are.
- The parent app addresses parents, the panel indicator talks to the child - use a friendly, simple tone there.
- Also add `Name[it]=`/`Comment[it]=` lines to `data/*.desktop` and the polkit `<message xml:lang="it">`.

## Code

- Python 3.8+, standard library plus PyGObject (GTK 3). No other runtime dependencies.
- Wrap every user-visible string in `_("...")` (or `N_("...")` for constants) and run
  `python3 tools/update-po.py` afterwards.
- The daemon returns error **codes**, never translated text; clients translate them (`common.ERRORS`).
- Run the tests before sending a pull request: `python3 -m unittest discover -s tests -t .`
- Build and try the package: `./tools/build-deb.sh && sudo apt install ./build/tuxsitter_*_all.deb`

## Release checklist

1. Bump `VERSION` in `src/tuxsitter/common.py` and add an entry to `CHANGELOG.md`.
2. Commit and push to `main`.
3. On GitHub: **Releases → Draft a new release**, create the tag (e.g. `v0.2.0`) on `main`, publish.
4. GitHub Actions builds the `.deb` and attaches it to the release automatically (takes about a minute).
