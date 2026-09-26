"""Load KiCad library symbols (flattening `extends`) and describe their pins."""
from __future__ import annotations

import copy
from dataclasses import dataclass
from pathlib import Path

from sexpr import Sym, find, find1, parse

KICAD_SYM = Path("/usr/share/kicad/symbols")
_cache: dict[Path, list] = {}


@dataclass
class Pin:
    number: str
    name: str
    etype: str
    x: float          # symbol coordinates, y up
    y: float
    angle: float      # direction from the connection point into the body
    unit: int


def _lib(path: Path):
    if path not in _cache:
        _cache[path] = parse(path.read_text())
    return _cache[path]


def _top_symbols(lib):
    return {s[1]: s for s in find(lib, "symbol")}


def load(lib_id: str, extra: Path | None = None):
    """Return a flattened copy of the symbol named lib_id ('Lib:Name'),
    renamed to lib_id with its units renamed Name_u_s, ready for lib_symbols."""
    libname, name = lib_id.split(":", 1)
    path = extra if extra is not None else KICAD_SYM / f"{libname}.kicad_sym"
    syms = _top_symbols(_lib(path))
    node = copy.deepcopy(syms[name])
    ext = find1(node, "extends")
    if ext is not None:
        parent = copy.deepcopy(syms[ext[1]])
        pname = ext[1]
        # properties from the child override the parent's
        child_props = {p[1]: p for p in find(node, "property")}
        merged = [parent[0], name]
        for item in parent[2:]:
            if isinstance(item, list) and item[0] == "property" and item[1] in child_props:
                merged.append(child_props.pop(item[1]))
            elif isinstance(item, list) and item[0] == "symbol":
                sub = copy.deepcopy(item)
                sub[1] = sub[1].replace(pname + "_", name + "_", 1)
                merged.append(sub)
            else:
                merged.append(item)
        # any child-only properties go after the parent's
        insert_at = max(i for i, it in enumerate(merged) if isinstance(it, list) and it[0] == "property") + 1
        for p in child_props.values():
            merged.insert(insert_at, p)
            insert_at += 1
        node = merged
    node[1] = lib_id
    return node


def pins(node) -> list[Pin]:
    out = []
    base = node[1].split(":", 1)[-1]
    for sub in find(node, "symbol"):
        # sub name: Base_unit_style
        suffix = sub[1][len(base) + 1:] if sub[1].startswith(base + "_") else sub[1].rsplit("_", 2)[-2] + "_" + sub[1].rsplit("_", 1)[-1]
        unit = int(suffix.split("_")[0])
        for p in find(sub, "pin"):
            at = find1(p, "at")
            out.append(Pin(number=find1(p, "number")[1], name=find1(p, "name")[1], etype=str(p[1]),
                           x=float(at[1]), y=float(at[2]), angle=float(at[3]) if len(at) > 3 else 0.0, unit=unit))
    return out


def prop(node, key):
    for p in find(node, "property"):
        if p[1] == key:
            return p[2]
    return None
