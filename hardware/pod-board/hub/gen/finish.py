"""Finish the routed hub board: silkscreen, no-connect net names, stackup,
and a final zone fill.

The component side is B.Cu; F.Cu carries the MIPI pairs and a ground pour,
so the board's name and the connector labels go on F.SilkS where nothing
covers them, and each connector is also named on B.SilkS beside it.
"""
from __future__ import annotations

import re
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

import pcbnew

import hub_design as D

MM = pcbnew.FromMM
TO = pcbnew.ToMM

# JLCPCB JLC04161H-7628: 1.6 mm, 1 oz outer, 0.5 oz inner, 7628 prepreg.
STACKUP = """(stackup
			(layer "F.SilkS" (type "Top Silk Screen") (color "White"))
			(layer "F.Paste" (type "Top Solder Paste"))
			(layer "F.Mask" (type "Top Solder Mask") (color "Green") (thickness 0.01))
			(layer "F.Cu" (type "copper") (thickness 0.035))
			(layer "dielectric 1" (type "prepreg") (thickness 0.2104) (material "7628") (epsilon_r 4.4) (loss_tangent 0.02))
			(layer "In1.Cu" (type "copper") (thickness 0.0152))
			(layer "dielectric 2" (type "core") (thickness 1.065) (material "FR4") (epsilon_r 4.6) (loss_tangent 0.02))
			(layer "In2.Cu" (type "copper") (thickness 0.0152))
			(layer "dielectric 3" (type "prepreg") (thickness 0.2104) (material "7628") (epsilon_r 4.4) (loss_tangent 0.02))
			(layer "B.Cu" (type "copper") (thickness 0.035))
			(layer "B.Mask" (type "Bottom Solder Mask") (color "Green") (thickness 0.01))
			(layer "B.Paste" (type "Bottom Solder Paste"))
			(layer "B.SilkS" (type "Bottom Silk Screen") (color "White"))
			(copper_finish "HAL lead-free")
			(dielectric_constraints no)
		)"""

# Each connector's label carries its reference, so no reference text is
# needed on the silkscreen; the assembly drawing (B.Fab) has them all.
LABELS = {
    "J1": "J1  CSI-A: baseboard J7", "J2": "J2  CSI-B: baseboard J8", "J3": "J3  EXT SYNC",
    "P1": "P1  FAR-L", "P2": "P2  NEAR-L", "P3": "P3  FAR-R", "P4": "P4  NEAR-R",
    "J4": "J4  FAR-L SYNC", "J5": "J5  NEAR-L SYNC", "J6": "J6  FAR-R SYNC", "J7": "J7  NEAR-R SYNC",
}
EDGE_GAP = 0.5      # silk to board edge
CLEAR = 0.2         # silk to pad openings and other silk


def text(b, s, x, y, layer, size=1.0, angle=0.0, mirror=False, bold=False):
    t = pcbnew.PCB_TEXT(b)
    t.SetText(s)
    t.SetPosition(pcbnew.VECTOR2I(MM(x), MM(y)))
    t.SetLayer(layer)
    t.SetTextSize(pcbnew.VECTOR2I(MM(size), MM(size)))
    t.SetTextThickness(MM(size * 0.15))
    t.SetTextAngleDegrees(angle)
    t.SetMirrored(mirror)
    t.SetBold(bold)
    b.Add(t)
    return t


def _box(item, grow=0.0):
    bb = item.GetBoundingBox()
    return (TO(bb.GetX()) - grow, TO(bb.GetY()) - grow, TO(bb.GetRight()) + grow, TO(bb.GetBottom()) + grow)


def _hit(a, c):
    return a[0] < c[2] and a[2] > c[0] and a[1] < c[3] and a[3] > c[1]


def obstacles(b, silk):
    """What silk on this layer must stay clear of: pad openings in that
    side's mask, holes, and the silkscreen already there."""
    mask = pcbnew.F_Mask if silk == pcbnew.F_SilkS else pcbnew.B_Mask
    out = []
    for fp in b.GetFootprints():
        for p in fp.Pads():
            if p.IsOnLayer(mask) or p.GetAttribute() == pcbnew.PAD_ATTRIB_NPTH:
                out.append(_box(p, CLEAR))
        for g in fp.GraphicalItems():
            if g.GetLayer() == silk and (not isinstance(g, pcbnew.PCB_TEXT) or g.IsVisible()):
                out.append(_box(g, CLEAR))
    for d in b.GetDrawings():
        if d.GetLayer() == silk:
            out.append(_box(d, CLEAR))
    return out


def place_label(b, s, fp, layer, size, frame, inward):
    """Put a label beside its connector on the first spot, stepping away
    from it, that clears pad openings, holes, other silk and the edge."""
    t = text(b, s, 0, 0, layer, size, mirror=(layer == pcbnew.B_SilkS))
    obs = obstacles(b, layer)
    obs = [o for o in obs if o != _box(t, CLEAR)]
    x0, y0, x1, y1 = frame
    cx0, cy0, cx1, cy1 = _box(fp)
    cx, cy = (cx0 + cx1) / 2, (cy0 + cy1) / 2
    w, h = (lambda bb: (bb[2] - bb[0], bb[3] - bb[1]))(_box(t))
    # candidate centres: the inward side first, then the two flanks
    sides = {"down": lambda d, a: (cx + a, cy1 + d + h / 2), "up": lambda d, a: (cx + a, cy0 - d - h / 2),
             "right": lambda d, a: (cx1 + d + w / 2, cy + a), "left": lambda d, a: (cx0 - d - w / 2, cy + a)}
    flanks = {"down": ("left", "right"), "up": ("left", "right"), "left": ("up", "down"), "right": ("up", "down")}[inward]
    order = [inward] + list(flanks)
    for step in range(0, 40):
        d = 0.3 + 0.25 * step
        for side in order:
            for a in (0.0, -1.0, 1.0, -2.0, 2.0, -3.0, 3.0, -4.0, 4.0, -6.0, 6.0):
                x, y = sides[side](d if side == inward else 0.3 + 0.25 * (step % 8), a)
                t.SetPosition(pcbnew.VECTOR2I(MM(x), MM(y)))
                bb = _box(t)
                if bb[0] < x0 + EDGE_GAP or bb[1] < y0 + EDGE_GAP or bb[2] > x1 - EDGE_GAP or bb[3] > y1 - EDGE_GAP:
                    continue
                if not any(_hit(bb, o) for o in obs):
                    return t
    print(f"  no clear spot for {s!r} on {b.GetLayerName(layer)}")
    return t


INWARD = {"J1": "down", "J2": "down", "J3": "down", "P1": "up", "P2": "up", "P3": "up", "P4": "up",
          "J4": "right", "J5": "right", "J6": "left", "J7": "left"}


def silkscreen(b):
    for fp in b.GetFootprints():
        fp.Reference().SetVisible(False)
        fp.Value().SetVisible(False)
    bb = b.GetBoardEdgesBoundingBox()
    frame = (TO(bb.GetX()), TO(bb.GetY()), TO(bb.GetRight()), TO(bb.GetBottom()))
    cx = (frame[0] + frame[2]) / 2
    # The top side carries no parts, so the name goes there. Each connector
    # is labelled on both sides, the bottom copy mirrored so it reads
    # correctly when the board is turned over.
    for s, y, size, bold in (("SIDELINE CAMERA HUB  rev A  2026-09", frame[1] + 17.0, 1.2, True),
                             ("parts on the other side; MIPI on this side", frame[1] + 19.2, 0.8, False)):
        t = text(b, s, cx, y, pcbnew.F_SilkS, size, bold=bold)
        if any(_hit(_box(t), o) for o in obstacles(b, pcbnew.F_SilkS) if o != _box(t, CLEAR)):
            print(f"  title line {s!r} crosses an opening or other silk")
    for ref, label in LABELS.items():
        fp = b.FindFootprintByReference(ref)
        size = 1.0 if ref in ("J1", "J2") or ref.startswith("P") else 0.8
        for layer in (pcbnew.F_SilkS, pcbnew.B_SilkS):
            place_label(b, label, fp, layer, size, frame, INWARD[ref])


def unconnected_nets(sch: Path) -> dict[tuple[str, str], str]:
    """The names KiCad gives no-connect pins ("unconnected-(J1-Pin_1-Pad1)"),
    read from the schematic's own netlist so the board matches it exactly."""
    with tempfile.TemporaryDirectory() as td:
        xml = Path(td) / "net.xml"
        subprocess.run(["kicad-cli", "sch", "export", "netlist", "--format", "kicadxml", "-o", str(xml), str(sch)],
                       check=True, capture_output=True)
        root = ET.parse(xml).getroot()
    out = {}
    for net in root.iter("net"):
        if net.get("name").startswith("unconnected-"):
            for node in net.iter("node"):
                out[(node.get("ref"), node.get("pin"))] = net.get("name")
    return out


def assign_unconnected(b, sch: Path):
    """Give no-connect pads their schematic nets, as KiCad's update from the
    schematic would. Done after routing, so the autorouter never sees them."""
    n = 0
    for (ref, pad), name in unconnected_nets(sch).items():
        fp = b.FindFootprintByReference(ref)
        if fp is None:
            continue
        net = b.FindNet(name)
        if net is None:
            net = pcbnew.NETINFO_ITEM(b, name)
            b.Add(net)
        for p in fp.Pads():
            if p.GetNumber() == pad:
                p.SetNet(net)
                n += 1
    print(f"  {n} no-connect pads named")


def set_stackup(path: Path):
    s = path.read_text()
    s = re.sub(r"\(stackup\b.*?\n\t\t\)", "", s, flags=re.S)
    s = s.replace("(setup\n", "(setup\n\t\t" + STACKUP + "\n", 1)
    path.write_text(s)


def main(path: Path):
    b = pcbnew.LoadBoard(str(path))
    silkscreen(b)
    assign_unconnected(b, path.with_suffix(".kicad_sch"))
    pcbnew.ZONE_FILLER(b).Fill(b.Zones())
    pcbnew.SaveBoard(str(path), b)
    set_stackup(path)
    # reload once so KiCad normalises the file with the stackup in place
    b = pcbnew.LoadBoard(str(path))
    pcbnew.ZONE_FILLER(b).Fill(b.Zones())
    pcbnew.SaveBoard(str(path), b)
    print("finished", path)


if __name__ == "__main__":
    main(Path(sys.argv[1]))
