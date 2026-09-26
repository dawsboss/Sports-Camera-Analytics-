"""Write the hub's KiCad 9 schematic from hub_design.PARTS.

Every pin gets a short wire and a net label, so the sheet reads as a
netlist laid out by block; ERC checks it like any hand-drawn sheet.
"""
from __future__ import annotations

import uuid
from pathlib import Path

import hub_design as D
import symlib
from sexpr import Sym, dump, find, find1

HERE = Path(__file__).parent
NS = uuid.UUID("6f1c2a52-8f4b-4d7e-9a53-0c5e6d9b1a10")
PROJECT = "sideline-cam-hub"
GRID = 2.54


def uid(*parts) -> str:
    return str(uuid.uuid5(NS, "/".join(map(str, parts))))


ROOT_UUID = uid("root")


def snap(v: float) -> float:
    return round(round(v / GRID) * GRID, 4)


def S(x):
    return Sym(x)


def effects(size=1.27, justify=None, hide=False):
    e = [S("effects"), [S("font"), [S("size"), size, size]]]
    if justify:
        e.append([S("justify")] + [S(j) for j in justify.split()])
    if hide:
        e.append([S("hide"), S("yes")])
    return e


def lib_path(lib_id):
    return HERE.parent / "sideline.kicad_sym" if lib_id.startswith("sideline:") else None


# Blocks: (x0, y0, width) regions on an A1 sheet, parts flow left to right.
REGIONS = {
    "inputs": (20, 25, 150),
    "ports": (185, 25, 190),
    "control": (390, 25, 190),
    "sync": (590, 25, 230),
    "power": (390, 330, 230),
    "sensors": (630, 330, 190),
    "test": (630, 470, 190),
}
LABEL_ROOM = 24.0


def extents(pins):
    xs = [p.x for p in pins] + [0.0]
    ys = [p.y for p in pins] + [0.0]
    return min(xs), max(xs), min(ys), max(ys)


def pin_end_sheet(ox, oy, p):
    return snap(ox + p.x) if False else round(ox + p.x, 4), round(oy - p.y, 4)


def stub_dir(angle):
    # pin angle points from the connection end into the body; wires go the other way
    return {0: (-1, 0), 90: (0, 1), 180: (1, 0), 270: (0, -1)}[int(angle) % 360]


def label_node(net, x, y, d):
    # Global labels, so each net is named exactly as hub_design names it
    # (a local label on the root sheet would name it "/NET") and the board's
    # nets match the schematic's for the parity check.
    ang = {(-1, 0): 180, (1, 0): 0, (0, 1): 270, (0, -1): 90}[d]
    just = {180: "right", 0: "left", 90: "left", 270: "right"}[ang]
    return [S("global_label"), net, [S("shape"), S("passive")], [S("at"), x, y, ang], effects(justify=just),
            [S("uuid"), uid("label", net, x, y)],
            [S("property"), "Intersheetrefs", "${INTERSHEET_REFS}", [S("at"), x, y, ang],
             effects(justify=just, hide=True)]]


def wire_node(x1, y1, x2, y2):
    return [S("wire"), [S("pts"), [S("xy"), x1, y1], [S("xy"), x2, y2]],
            [S("stroke"), [S("width"), 0], [S("type"), S("default")]], [S("uuid"), uid("wire", x1, y1, x2, y2)]]


def symbol_instance(part_ref, lib_id, libnode, x, y, props, pins_list, in_bom=True, on_board=True, dnp=False, unit=1):
    node = [S("symbol"), [S("lib_id"), lib_id], [S("at"), x, y, 0], [S("unit"), unit],
            [S("exclude_from_sim"), S("no")], [S("in_bom"), S("yes" if in_bom else "no")],
            [S("on_board"), S("yes" if on_board else "no")], [S("dnp"), S("yes" if dnp else "no")],
            [S("fields_autoplaced"), S("yes")], [S("uuid"), uid("sym", part_ref)]]
    for key, (val, px, py, hide) in props.items():
        node.append([S("property"), key, val, [S("at"), px, py, 0], effects(hide=hide, justify=None if not hide else None)])
    for p in pins_list:
        node.append([S("pin"), p.number, [S("uuid"), uid("pin", part_ref, p.number, p.unit)]])
    node.append([S("instances"), [S("project"), PROJECT,
                                  [S("path"), "/" + ROOT_UUID, [S("reference"), part_ref], [S("unit"), unit]]]])
    return node


def build():
    items = []
    libs = {}
    for p in D.PARTS:
        if p.lib_id not in libs:
            libs[p.lib_id] = symlib.load(p.lib_id, lib_path(p.lib_id))
    libs["power:PWR_FLAG"] = symlib.load("power:PWR_FLAG")

    cursor = {g: [x0, y0, 0.0] for g, (x0, y0, w) in REGIONS.items()}
    placed = {}
    for part in D.PARTS:
        node = libs[part.lib_id]
        ps = [p for p in symlib.pins(node) if p.unit in (0, 1)]
        x0, x1, y0, y1 = extents(ps)
        w = (x1 - x0) + 2 * LABEL_ROOM
        h = (y1 - y0) + 14
        gx, gy, gw = REGIONS[part.group]
        cx, cy, row_h = cursor[part.group]
        if cx + w > gx + gw and cx > gx:
            cx, cy, row_h = gx, cy + row_h, 0.0
        ox = snap(cx + LABEL_ROOM - x0)
        oy = snap(cy + 7 + y1)
        cursor[part.group] = [cx + w, cy, max(row_h, h)]
        placed[part.ref] = (ox, oy)

        props = {
            "Reference": (part.ref, round(ox + x1 + 1.27, 2), round(oy - y1 - 2.54, 2), False),
            "Value": (part.value, round(ox + x1 + 1.27, 2), round(oy + (-y0) + 3.81, 2), False),
            "Footprint": (part.footprint, ox, oy, True),
            "Datasheet": (symlib.prop(node, "Datasheet") or "~", ox, oy, True),
            "Description": (part.descr or (symlib.prop(node, "Description") or ""), ox, oy, True),
            "MPN": (part.mpn, ox, oy, True),
            "LCSC": (part.lcsc, ox, oy, True),
        }
        in_bom = not part.ref.startswith(("TP", "H", "JP"))     # copper only, nothing to buy
        items.append(symbol_instance(part.ref, part.lib_id, node, ox, oy, props, ps, in_bom=in_bom, dnp=part.dnp))
        for p in ps:
            ex, ey = round(ox + p.x, 4), round(oy - p.y, 4)
            net = part.nets.get(p.number)
            if net is None:
                items.append([S("no_connect"), [S("at"), ex, ey], [S("uuid"), uid("nc", part.ref, p.number)]])
                continue
            dx, dy = stub_dir(p.angle)
            sx, sy = round(ex + dx * GRID, 4), round(ey + dy * GRID, 4)
            items.append(wire_node(ex, ey, sx, sy))
            items.append(label_node(net, sx, sy, (dx, dy)))

    # power flags
    fx, fy = REGIONS["test"][0], REGIONS["test"][1] + 60
    for i, net in enumerate(D.FLAGS):
        ox, oy = snap(fx + 10 + i * 30), snap(fy)
        node = libs["power:PWR_FLAG"]
        ps = symlib.pins(node)
        props = {"Reference": (f"#FLG0{i + 1}", ox, oy - 5.08, True), "Value": ("PWR_FLAG", ox, oy - 3.81, False),
                 "Footprint": ("", ox, oy, True), "Datasheet": ("~", ox, oy, True), "Description": ("", ox, oy, True)}
        items.append(symbol_instance(f"#FLG0{i + 1}", "power:PWR_FLAG", node, ox, oy, props, ps, in_bom=False, on_board=False))
        items.append(wire_node(ox, oy, ox, round(oy + GRID, 4)))
        items.append(label_node(net, ox, round(oy + GRID, 4), (0, 1)))

    notes = [
        "Sideline camera hub, rev A. Plugs into Antmicro's Jetson Orin Baseboard J7 (J1 here) and J8 (J2 here).",
        "Baseboard stuffing for four 2-lane cameras: fit R122, remove R108 (CSI3 lanes to J8). Fit R4/R5 only if J2's GPIOs are wanted.",
        "FFC: 50-pin 0.5 mm, pin N to pin N, as Antmicro's own camera boards use. Camera ports follow the Raspberry Pi 5 22-pin pinout.",
        "Sync: SN74AVC4T774 DIR high = bus to head (slave), low = head to bus (master). PCA9555 pull-ups start every head powered,",
        "enabled and isolated (OE high). Software sets one master (or none, with an external source on J3), then clears OE.",
        "I2C (via the baseboard's mux): FAR-L bus has U1 PCA9555 0x20, U10 ICM-42688-P 0x69, U11 SHT45 0x44; FAR-R bus has U2 PCA9555 0x20.",
    ]
    for i, t in enumerate(notes):
        items.append([S("text"), t, [S("exclude_from_sim"), S("no")], [S("at"), 20, 520 + i * 6, 0],
                      effects(size=2.0, justify="left bottom"), [S("uuid"), uid("note", i)]])

    sch = [S("kicad_sch"), [S("version"), 20250114], [S("generator"), "eeschema"], [S("generator_version"), "9.0"],
           [S("uuid"), ROOT_UUID], [S("paper"), "A1"],
           [S("title_block"), [S("title"), "Sideline camera hub"], [S("date"), "2026-09-26"], [S("rev"), "A"],
            [S("company"), "Sports Camera Analytics"],
            [S("comment"), 1, "Four IMX678 heads to Antmicro Jetson Orin Baseboard (Mixtile Core 3588E)"],
            [S("comment"), 2, "Generated from hardware/pod-board/hub/gen/hub_design.py"]],
           [S("lib_symbols")] + list(libs.values())]
    sch += items
    sch.append([S("sheet_instances"), [S("path"), "/", [S("page"), "1"]]])
    sch.append([S("embedded_fonts"), S("no")])
    return sch, placed


def main(out: Path):
    sch, _ = build()
    out.write_text(dump(sch) + "\n")
    print("wrote", out)


if __name__ == "__main__":
    import sys
    main(Path(sys.argv[1]) if len(sys.argv) > 1 else HERE.parent / f"{PROJECT}.kicad_sch")
