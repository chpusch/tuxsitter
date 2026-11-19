"""Minimal .po reader/writer and .mo compiler (no gettext tools needed)."""
import ast
import struct

HEADER = (
    "Project-Id-Version: tuxsitter\n"
    "Report-Msgid-Bugs-To: https://github.com/chpusch/tuxsitter/issues\n"
    "Language: {lang}\n"
    "MIME-Version: 1.0\n"
    "Content-Type: text/plain; charset=UTF-8\n"
    "Content-Transfer-Encoding: 8bit\n"
    "Plural-Forms: nplurals=2; plural=(n != 1);\n"
)


def _unquote(line):
    return ast.literal_eval(line)


def read_po(path):
    """Return {msgid: msgstr}; fuzzy entries are ignored."""
    out, mid, mstr, cur, fuzzy = {}, None, None, None, False

    def flush():
        if mid is not None and mid != "" and not fuzzy:
            out[mid] = mstr or ""

    try:
        with open(path, encoding="utf-8") as f:
            lines = f.read().splitlines()
    except OSError:
        return out
    for raw in lines + [""]:
        line = raw.strip()
        if not line:
            flush()
            mid, mstr, cur, fuzzy = None, None, None, False
        elif line.startswith("#,") and "fuzzy" in line:
            fuzzy = True
        elif line.startswith("#"):
            continue
        elif line.startswith("msgid "):
            mid, cur = _unquote(line[6:]), "id"
        elif line.startswith("msgstr "):
            mstr, cur = _unquote(line[7:]), "str"
        elif line.startswith('"'):
            if cur == "id":
                mid += _unquote(line)
            elif cur == "str":
                mstr += _unquote(line)
    return out


def _quote(s):
    s = s.replace("\\", "\\\\").replace('"', '\\"').replace("\t", "\\t")
    parts = s.split("\n")
    if len(parts) == 1:
        return '"%s"' % s
    chunks = [p + "\\n" for p in parts[:-1]] + ([parts[-1]] if parts[-1] else [])
    return '""\n' + "\n".join('"%s"' % c for c in chunks)


def write_po(path, entries, lang=None):
    """entries: [(refs, msgid, msgstr)]"""
    hdr = HEADER.format(lang=lang or "")
    out = ["# Tuxsitter translations", "# This file is distributed under the GPL-3.0-or-later license.", "#",
           'msgid ""', "msgstr " + _quote(hdr), ""]
    for refs, mid, mstr in entries:
        for r in refs[:3]:
            out.append("#: " + r)
        if "{" in mid:
            out.append("#, python-brace-format")
        out.append("msgid " + _quote(mid))
        out.append("msgstr " + _quote(mstr))
        out.append("")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(out))


def compile_mo(po_path, mo_path, lang):
    cat = {k: v for k, v in read_po(po_path).items() if v}
    cat[""] = HEADER.format(lang=lang)
    keys = sorted(cat)
    ids = b"".join(k.encode() + b"\0" for k in keys)
    strs = b"".join(cat[k].encode() + b"\0" for k in keys)
    n = len(keys)
    o_ids = 7 * 4
    o_strs = o_ids + n * 8
    data_start = o_strs + n * 8
    id_tab, str_tab, pos = [], [], data_start
    for k in keys:
        b = k.encode()
        id_tab.append((len(b), pos))
        pos += len(b) + 1
    for k in keys:
        b = cat[k].encode()
        str_tab.append((len(b), pos))
        pos += len(b) + 1
    out = struct.pack("<7I", 0x950412DE, 0, n, o_ids, o_strs, 0, 0)
    out += b"".join(struct.pack("<2I", *t) for t in id_tab)
    out += b"".join(struct.pack("<2I", *t) for t in str_tab)
    out += ids + strs
    with open(mo_path, "wb") as f:
        f.write(out)
    return len(cat) - 1
