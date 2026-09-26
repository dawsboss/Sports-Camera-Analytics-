"""Place, route and pour the hub's board from hub_design.PARTS.

Everything is assembled on the bottom side (B.Cu), like Antmicro's own
camera boards, so the 50-pin FFC maps pin N to pin N the way it does for
them. The MIPI pairs are routed here by hand-written geometry (mipi.py) on
F.Cu, over the In1 ground plane, which leaves B.Cu to the parts. The pairs
drop to B.Cu only at the connectors, and the one lane swap each head needs
(the 50-pin order is D1, D0, CLK and the Raspberry Pi order is D0, D1, CLK)
passes D1 under D0 on B.Cu. The small parts are placed by place.py and
everything else is left for Freerouting (see route.py).
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import pcbnew

import hub_design as D
import make_sch

HERE = Path(__file__).parent
OUT = HERE.parent
PROJECT = make_sch.PROJECT
KFP = Path("/usr/share/kicad/footprints")

MM = pcbnew.FromMM


def V(x, y):
    return pcbnew.VECTOR2I(MM(x), MM(y))


# ---------------------------------------------------------------- board frame
X0, Y0 = 100.0, 100.0
W, H = 84.0, 52.0
X1, Y1 = X0 + W, Y0 + H

# Connectors. The 50-pin inputs sit on the top edge, cable entering from
# above; the camera ports on the bottom edge, cable leaving downwards.
J1X, J2X, JY = X0 + 16.35, X1 - 17.35, Y0 + 3.7
PORTS = {"NEARL": X0 + 10.5, "FARL": X0 + 31.0, "NEARR": X0 + 53.0, "FARR": X0 + 71.0}
PY = Y1 - 4.9          # the FH12 outline 0.4 mm inside the edge

# Sync connectors: heads on the left and right edges, the external source on
# the top edge. (edge, position along it); the cable faces out of that edge.
EDGE_CONN = {"J4": ("left", Y0 + 18.5), "J5": ("left", Y0 + 29.5),
             "J6": ("right", Y0 + 18.5), "J7": ("right", Y0 + 29.5), "J3": ("top", X0 + W / 2)}


def fp_lib(fpid: str) -> tuple[str, str]:
    lib, name = fpid.split(":", 1)
    path = OUT / "sideline.pretty" if lib == "sideline" else KFP / f"{lib}.pretty"
    return str(path), name


def new_board():
    b = pcbnew.NewBoard(str(OUT / f"{PROJECT}.kicad_pcb"))
    b.SetCopperLayerCount(4)
    ds = b.GetDesignSettings()
    ds.m_MinClearance = MM(0.127)
    ds.m_TrackMinWidth = MM(0.127)
    ds.m_ViasMinSize = MM(0.5)
    ds.m_MinThroughDrill = MM(0.25)
    ds.m_CopperEdgeClearance = MM(0.3)
    ds.m_HoleToHoleMin = MM(0.25)
    ds.m_HoleClearance = MM(0.2)
    ns = ds.m_NetSettings
    # NewBoard reuses an existing project file's net settings; start clean
    # so a rerun cannot inherit stale classes or patterns.
    ns.ClearNetclasses()
    ns.ClearNetclassPatternAssignments()
    dflt = ns.GetDefaultNetclass()
    dflt.SetClearance(MM(0.127)); dflt.SetTrackWidth(MM(0.2)); dflt.SetViaDiameter(MM(0.6)); dflt.SetViaDrill(MM(0.3))
    mipi = pcbnew.NETCLASS("MIPI")
    mipi.SetClearance(MM(0.127)); mipi.SetTrackWidth(MM(0.25)); mipi.SetViaDiameter(MM(0.6)); mipi.SetViaDrill(MM(0.3))
    mipi.SetDiffPairWidth(MM(0.25)); mipi.SetDiffPairGap(MM(0.25)); mipi.SetDiffPairViaGap(MM(0.25))
    ns.SetNetclass("MIPI", mipi)
    power = pcbnew.NETCLASS("Power")
    power.SetClearance(MM(0.127)); power.SetTrackWidth(MM(0.25)); power.SetViaDiameter(MM(0.6)); power.SetViaDrill(MM(0.3))
    ns.SetNetclass("Power", power)
    for pat in ("*_D0_P", "*_D0_N", "*_D1_P", "*_D1_N", "*_CK_P", "*_CK_N"):
        ns.SetNetclassPatternAssignment(pat, "MIPI")
    # Supplies at 0.25 mm: the widest that still leaves a 0.5 mm-pitch pad
    # row clear. Ground reaches the planes through a via at every pad.
    for pat in ("+3V3A", "+3V3B", "+1V8", "*_3V3", "VIO_HEAD"):
        ns.SetNetclassPatternAssignment(pat, "Power")
    # Ground has its own class so the autorouter can be told to leave it to
    # the planes and the per-pad vias.
    gnd = pcbnew.NETCLASS("GND")
    gnd.SetClearance(MM(0.127)); gnd.SetTrackWidth(MM(0.25)); gnd.SetViaDiameter(MM(0.6)); gnd.SetViaDrill(MM(0.3))
    ns.SetNetclass("GND", gnd)
    ns.SetNetclassPatternAssignment("GND", "GND")
    return b


CORNER_R = 1.5


def outline(b):
    r = CORNER_R
    def seg(x1, y1, x2, y2):
        s = pcbnew.PCB_SHAPE(b); s.SetShape(pcbnew.SHAPE_T_SEGMENT); s.SetLayer(pcbnew.Edge_Cuts)
        s.SetStart(V(x1, y1)); s.SetEnd(V(x2, y2)); s.SetWidth(MM(0.05)); b.Add(s)
    def arc(cx, cy, a0):
        s = pcbnew.PCB_SHAPE(b); s.SetShape(pcbnew.SHAPE_T_ARC); s.SetLayer(pcbnew.Edge_Cuts)
        s.SetCenter(V(cx, cy)); s.SetStart(V(cx + r * math.cos(math.radians(a0)), cy + r * math.sin(math.radians(a0))))
        s.SetArcAngleAndEnd(pcbnew.EDA_ANGLE(90, pcbnew.DEGREES_T)); s.SetWidth(MM(0.05)); b.Add(s)
    seg(X0 + r, Y0, X1 - r, Y0); seg(X1, Y0 + r, X1, Y1 - r); seg(X1 - r, Y1, X0 + r, Y1); seg(X0, Y1 - r, X0, Y0 + r)
    arc(X1 - r, Y0 + r, 270); arc(X1 - r, Y1 - r, 0); arc(X0 + r, Y1 - r, 90); arc(X0 + r, Y0 + r, 180)


def place_all(b, placements):
    nets = {}
    for n in sorted(D.nets()):
        ni = pcbnew.NETINFO_ITEM(b, n); b.Add(ni); nets[n] = ni
    fps = {}
    for part in D.PARTS:
        lib, name = fp_lib(part.footprint)
        fp = pcbnew.FootprintLoad(lib, name)
        if fp is None:
            raise SystemExit(f"footprint {part.footprint} not found")
        fp.SetFPID(pcbnew.LIB_ID(part.footprint.split(":")[0], name))
        fp.SetReference(part.ref); fp.SetValue(part.value)
        for key, val in (("MPN", part.mpn), ("LCSC", part.lcsc), ("Description", part.descr)):
            if val:
                fp.SetField(key, val)
                fp.GetFieldByName(key).SetVisible(False)
        fp.SetPath(pcbnew.KIID_PATH("/" + make_sch.uid("sym", part.ref)))
        fp.SetSheetname("Root"); fp.SetSheetfile(f"{PROJECT}.kicad_sch")
        b.Add(fp)
        x, y, rot = placements[part.ref]
        fp.SetPosition(V(x, y))
        fp.Flip(fp.GetPosition(), pcbnew.FLIP_DIRECTION_LEFT_RIGHT)
        fp.SetOrientationDegrees(180 + rot)
        if part.ref.startswith(("TP", "H")):
            fp.SetExcludedFromBOM(True); fp.SetExcludedFromPosFiles(True)
        for pad in fp.Pads():
            n = part.nets.get(pad.GetNumber())
            if n is not None:
                pad.SetNet(nets[n])
        fps[part.ref] = fp
    return nets, fps


def default_placements():
    pl = {"J1": (J1X, JY, 0), "J2": (J2X, JY, 0)}
    for h, x in PORTS.items():
        pl[D.PORT[h]] = (x, PY, 0)
    # the rest start off-board; place.py and place_edge_connectors() move them
    for p in D.PARTS:
        pl.setdefault(p.ref, (X1 + 20, Y1 + 20, 0))
    return pl


def place_edge_connectors(fps):
    """Turn each SH connector until its mounting tabs (the mouth) face out of
    its edge, then push it against that edge."""
    out = {"left": (-1, 0), "right": (1, 0), "top": (0, -1)}
    for ref, (edge, along) in EDGE_CONN.items():
        fp = fps[ref]
        best = None
        for rot in (0, 90, 180, 270):
            fp.SetOrientationDegrees(rot)
            fp.SetPosition(V(0, 0))
            sig = [p for p in fp.Pads() if p.GetNumber().isdigit()]
            mp = [p for p in fp.Pads() if not p.GetNumber().isdigit()]
            sx = sum(pcbnew.ToMM(p.GetPosition().x) for p in sig) / len(sig)
            sy = sum(pcbnew.ToMM(p.GetPosition().y) for p in sig) / len(sig)
            mx = sum(pcbnew.ToMM(p.GetPosition().x) for p in mp) / len(mp)
            my = sum(pcbnew.ToMM(p.GetPosition().y) for p in mp) / len(mp)
            dx, dy = out[edge]
            score = (mx - sx) * dx + (my - sy) * dy
            if best is None or score > best[0]:
                best = (score, rot)
        fp.SetOrientationDegrees(best[1])
        fp.SetPosition(V(0, 0))
        # Keep every pad 0.35 mm inside the edge; the housing's mouth then
        # sits at the edge.
        xs0 = min(pcbnew.ToMM(p.GetBoundingBox().GetX()) for p in fp.Pads())
        xs1 = max(pcbnew.ToMM(p.GetBoundingBox().GetRight()) for p in fp.Pads())
        ys0 = min(pcbnew.ToMM(p.GetBoundingBox().GetY()) for p in fp.Pads())
        gap = 0.35
        if edge == "left":
            fp.SetPosition(V(X0 + gap - xs0, along))
        elif edge == "right":
            fp.SetPosition(V(X1 - gap - xs1, along))
        else:
            fp.SetPosition(V(along, Y0 + gap - ys0))


def pad_xy(fp, net):
    for pad in fp.Pads():
        if pad.GetNetname() == net:
            return pcbnew.ToMM(pad.GetPosition().x), pcbnew.ToMM(pad.GetPosition().y)
    raise KeyError(net)


# D1 drops to B.Cu this far above the port row, far enough for its crossing
# under D0 to clear D0's and CK's own drops at the port.
HOP_ABOVE_PORT = 8.1


def route_mipi(b, nets, fps, start_y=110.6, hop_y=PY - HOP_ABOVE_PORT):
    import mipi
    r = mipi.Router(b, nets)
    info = {}
    for h in D.HEADS:
        top_fp = fps["J1" if h in ("FARL", "NEARL") else "J2"]
        bot_fp = fps[D.PORT[h]]
        top, bot = {}, {}
        for pair in ("CK", "D0", "D1"):
            (xp, yt), (xn, _) = pad_xy(top_fp, f"{h}_{pair}_P"), pad_xy(top_fp, f"{h}_{pair}_N")
            top[pair] = (xp + xn) / 2
            (xp, yb), (xn, _) = pad_xy(bot_fp, f"{h}_{pair}_P"), pad_xy(bot_fp, f"{h}_{pair}_N")
            bot[pair] = (xp + xn) / 2
        s = 1 if bot["CK"] > top["CK"] else -1
        info[h] = mipi.head(r, h, top, bot, s, yt, yb, start_y, hop_y)
    for ref in ("J1", "J2"):
        mipi.connector_ground(r, fps[ref], -1.5)
    for h in D.HEADS:
        mipi.connector_ground(r, fps[D.PORT[h]], 1.55)
    return r, info


# An M2 screw head is 3.8 mm across; keep tracks and vias out from under it
# on both outer layers. Ground pour may run there.
HOLE_KEEPOUT_R = 2.2


def hole_keepouts(b, fps):
    for ref in ("H1", "H2", "H3"):
        c = fps[ref].GetPosition()
        z = pcbnew.ZONE(b)
        z.SetIsRuleArea(True)
        z.SetDoNotAllowTracks(True)
        z.SetDoNotAllowVias(True)
        z.SetDoNotAllowPads(False)
        z.SetDoNotAllowFootprints(False)
        z.SetDoNotAllowCopperPour(False)
        ls = pcbnew.LSET()
        ls.AddLayer(pcbnew.F_Cu)
        ls.AddLayer(pcbnew.B_Cu)
        z.SetLayerSet(ls)
        ol = z.Outline()
        ol.NewOutline()
        for i in range(24):
            a = 2 * math.pi * i / 24
            ol.Append(c.x + MM(HOLE_KEEPOUT_R * math.cos(a)), c.y + MM(HOLE_KEEPOUT_R * math.sin(a)))
        z.SetZoneName(f"{ref} screw head")
        b.Add(z)


# Custom design rules, read by KiCad from beside the project. Every ground
# pad has its own via into the planes, so one thermal spoke to a pour is
# enough where a tight spot leaves room for no more.
DRU = """(version 1)
(rule "one spoke where the pad has its own ground via"
  (condition "A.NetName == 'GND'")
  (constraint min_resolved_spokes 1))
"""


def in2_under_mipi(b, mipi_items, margin=0.5):
    """In2 carries some slow signals, so keep its tracks out from under the
    MIPI pairs' short B.Cu runs (at the 50-pin connectors and each D1
    crossing): In2 stays solid ground under them, as In1 does under F.Cu."""
    groups = {}
    for it in mipi_items:
        if isinstance(it, pcbnew.PCB_VIA) or it.GetLayer() != pcbnew.B_Cu or it.GetNetname() == "GND":
            continue
        head = it.GetNetname().split("_")[0]
        top = pcbnew.ToMM(it.GetStart().y) < Y0 + H / 2
        bb = it.GetBoundingBox()
        box = (pcbnew.ToMM(bb.GetX()), pcbnew.ToMM(bb.GetY()), pcbnew.ToMM(bb.GetRight()), pcbnew.ToMM(bb.GetBottom()))
        g = groups.get((head, top))
        groups[(head, top)] = box if g is None else (min(g[0], box[0]), min(g[1], box[1]), max(g[2], box[2]), max(g[3], box[3]))
    for (head, top), (x0, y0, x1, y1) in sorted(groups.items()):
        z = pcbnew.ZONE(b)
        z.SetIsRuleArea(True)
        z.SetDoNotAllowTracks(True)
        z.SetDoNotAllowVias(False)
        z.SetDoNotAllowPads(False)
        z.SetDoNotAllowFootprints(False)
        z.SetDoNotAllowCopperPour(False)
        z.SetLayer(pcbnew.In2_Cu)
        ol = z.Outline()
        ol.NewOutline()
        for x, y in ((x0 - margin, y0 - margin), (x1 + margin, y0 - margin), (x1 + margin, y1 + margin), (x0 - margin, y1 + margin)):
            ol.Append(MM(max(x, X0)), MM(max(min(y, Y1), Y0)))
        z.SetZoneName(f"{head} MIPI {'top' if top else 'port'}: In2 solid")
        b.Add(z)


EDGE_BAND = 0.35


def edge_keepout(b):
    """A band just inside the outline with no tracks or vias. KiCad wants
    copper 0.3 mm from the edge; Freerouting knows only its own clearance,
    so without the band it runs tracks along the edge."""
    z = pcbnew.ZONE(b)
    z.SetIsRuleArea(True)
    z.SetDoNotAllowTracks(True)
    z.SetDoNotAllowVias(True)
    z.SetDoNotAllowPads(False)
    z.SetDoNotAllowFootprints(False)
    z.SetDoNotAllowCopperPour(False)
    ls = pcbnew.LSET()
    for layer in (pcbnew.F_Cu, pcbnew.In2_Cu, pcbnew.B_Cu):
        ls.AddLayer(layer)
    z.SetLayerSet(ls)
    ol = z.Outline()
    ol.NewOutline()
    for x, y in ((X0 - 0.2, Y0 - 0.2), (X1 + 0.2, Y0 - 0.2), (X1 + 0.2, Y1 + 0.2), (X0 - 0.2, Y1 + 0.2)):
        ol.Append(MM(x), MM(y))
    ol.NewHole()
    r = CORNER_R - EDGE_BAND
    for cx, cy, a0 in ((X1 - CORNER_R, Y0 + CORNER_R, -90), (X1 - CORNER_R, Y1 - CORNER_R, 0),
                       (X0 + CORNER_R, Y1 - CORNER_R, 90), (X0 + CORNER_R, Y0 + CORNER_R, 180)):
        for i in range(7):
            a = math.radians(a0 + 15 * i)
            ol.Append(MM(cx + r * math.cos(a)), MM(cy + r * math.sin(a)), 0, 0)    # outline 0, hole 0
    z.SetZoneName("edge band")
    b.Add(z)


def save(b, path=None):
    path = str(path or OUT / f"{PROJECT}.kicad_pcb")
    pcbnew.SaveBoard(path, b)
    (Path(path).parent / f"{PROJECT}.kicad_dru").write_text(DRU)
    return path


if __name__ == "__main__":
    b = new_board()
    outline(b)
    nets, fps = place_all(b, default_placements())
    place_edge_connectors(fps)
    r, info = route_mipi(b, nets, fps)
    print({h: {k: round(v, 2) for k, v in i.items()} for h, i in info.items()})
    import place
    fixed = ["J1", "J2", "P1", "P2", "P3", "P4", "J3", "J4", "J5", "J6", "J7"]
    # parts sit on B.Cu, so only the MIPI copper on B.Cu and the vias are in their way
    blocking = [it for it in r.items if isinstance(it, pcbnew.PCB_VIA) or it.GetLayer() == pcbnew.B_Cu]
    place.run(b, fps, fixed, blocking, (X0, Y0, X1, Y1), hole_items=r.items)
    hole_keepouts(b, fps)
    edge_keepout(b)
    in2_under_mipi(b, r.items)
    print("saved", save(b))
