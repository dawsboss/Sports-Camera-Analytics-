"""Minimal S-expression reader/writer for KiCad files."""
from __future__ import annotations

import re

_TOKEN = re.compile(r'\s*(?:(\()|(\))|"((?:[^"\\]|\\.)*)"|([^\s()"]+))', re.S)


class Sym(str):
    """An unquoted atom."""


def parse(text: str):
    stack = [[]]
    pos = 0
    n = len(text)
    while pos < n:
        m = _TOKEN.match(text, pos)
        if not m:
            if text[pos:].strip() == "":
                break
            raise ValueError(f"bad token at {pos}: {text[pos:pos+40]!r}")
        pos = m.end()
        if m.group(1):
            stack.append([])
        elif m.group(2):
            done = stack.pop()
            stack[-1].append(done)
        elif m.group(3) is not None:
            stack[-1].append(bytes(m.group(3), "utf-8").decode("unicode_escape") if "\\" in m.group(3) else m.group(3))
        else:
            stack[-1].append(Sym(m.group(4)))
    return stack[0][0] if len(stack[0]) == 1 else stack[0]


def q(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n") + '"'


def dump(node, indent: int = 0) -> str:
    """Serialise with KiCad-like layout: one child list per line, tabs."""
    if not isinstance(node, list):
        return str(node) if isinstance(node, Sym) else (q(node) if isinstance(node, str) else fmt_num(node))
    head = node[0]
    atoms = []
    kids = []
    for x in node[1:]:
        (kids if isinstance(x, list) else atoms).append(x)
    pad = "\t" * indent
    parts = [dump(head)] + [dump(a) for a in atoms]
    simple = all(not isinstance(k, list) or all(not isinstance(z, list) for z in k[1:]) for k in kids) and len(kids) <= 3 \
        and head in ("at", "xy", "pts", "size", "font", "stroke", "fill", "offset", "length", "effects", "start", "end", "mid", "center")
    if not kids:
        return "(" + " ".join(parts) + ")"
    if simple:
        return "(" + " ".join(parts + [dump(k) for k in kids]) + ")"
    out = "(" + " ".join(parts)
    for k in kids:
        out += "\n" + pad + "\t" + dump(k, indent + 1)
    out += "\n" + pad + ")"
    return out


def fmt_num(v) -> str:
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, int):
        return str(v)
    s = f"{v:.4f}".rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


def find(node, key):
    return [c for c in node[1:] if isinstance(c, list) and c and c[0] == key]


def find1(node, key):
    r = find(node, key)
    return r[0] if r else None
