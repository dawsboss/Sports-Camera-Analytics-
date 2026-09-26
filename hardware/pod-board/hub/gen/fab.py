"""Check the hub and write everything a board house needs.

    python fab.py <hub dir>

Runs ERC on the schematic, DRC with the schematic-parity check on the
board, and writes fab/: Gerbers and drill (zipped), a BOM and a placement
file in JLCPCB's column names, the schematic as PDF, a bottom assembly
drawing, both reports, and renders into renders/.
"""
from __future__ import annotations

import csv
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

import render

NAME = "sideline-cam-hub"
LAYERS = "F.Cu,In1.Cu,In2.Cu,B.Cu,F.Paste,B.Paste,F.SilkS,B.SilkS,F.Mask,B.Mask,Edge.Cuts"


def run(*cmd, ok_codes=(0,)):
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode not in ok_codes:
        raise SystemExit(f"{' '.join(cmd)} failed:\n{r.stdout}\n{r.stderr}")
    return r


def main(hub: Path):
    sch, pcb = hub / f"{NAME}.kicad_sch", hub / f"{NAME}.kicad_pcb"
    fab, renders = hub / "fab", hub / "renders"
    fab.mkdir(exist_ok=True)
    renders.mkdir(exist_ok=True)

    run("kicad-cli", "sch", "erc", "--severity-all", "--format", "report", "-o", str(fab / "erc.rpt"), str(sch), ok_codes=(0, 5))
    run("kicad-cli", "pcb", "drc", "--schematic-parity", "--severity-all", "--format", "report",
        "-o", str(fab / "drc.rpt"), str(pcb), ok_codes=(0, 5))

    gerb = fab / "gerbers"
    shutil.rmtree(gerb, ignore_errors=True)
    gerb.mkdir()
    run("kicad-cli", "pcb", "export", "gerbers", "--layers", LAYERS, "--subtract-soldermask", "-o", str(gerb) + "/", str(pcb))
    run("kicad-cli", "pcb", "export", "drill", "--format", "excellon", "--excellon-separate-th", "--generate-map",
        "--map-format", "pdf", "-o", str(gerb) + "/", str(pcb))
    with zipfile.ZipFile(fab / f"{NAME}-gerbers.zip", "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(gerb.iterdir()):
            z.write(f, f.name)
    shutil.rmtree(gerb)

    run("kicad-cli", "sch", "export", "bom", "--fields", "Value,Reference,Footprint,MPN,LCSC,${QUANTITY}",
        "--labels", "Comment,Designator,Footprint,MPN,LCSC Part #,Quantity", "--group-by", "Value,Footprint,MPN",
        "--exclude-dnp", "-o", str(fab / f"{NAME}-bom.csv"), str(sch))
    pos = fab / "pos-raw.csv"
    run("kicad-cli", "pcb", "export", "pos", "--format", "csv", "--units", "mm", "--side", "both",
        "--exclude-dnp", "-o", str(pos), str(pcb))
    with open(pos) as f, open(fab / f"{NAME}-cpl.csv", "w", newline="") as g:
        w = csv.writer(g)
        w.writerow(["Designator", "Mid X", "Mid Y", "Layer", "Rotation"])
        for row in csv.DictReader(f):
            w.writerow([row["Ref"], f"{float(row['PosX']):.3f}mm", f"{float(row['PosY']):.3f}mm",
                        "Bottom" if row["Side"].lower().startswith("b") else "Top", f"{float(row['Rot']):.1f}"])
    pos.unlink()
    run("kicad-cli", "sch", "export", "pdf", "-o", str(fab / f"{NAME}-schematic.pdf"), str(sch))
    run("kicad-cli", "pcb", "export", "pdf", "--layers", "B.Fab,Edge.Cuts", "--mode-single", "--mirror",
        "-o", str(fab / f"{NAME}-assembly-bottom.pdf"), str(pcb))

    for side, rot, out in (("bottom", "", "hub_bottom.png"), ("top", "", "hub_top.png")):
        cmd = ["kicad-cli", "pcb", "render", "--side", side, "--quality", "high", "-w", "1600", "-h", "1000",
               "--background", "opaque", "-o", str(renders / out), str(pcb)]
        run(*cmd)
    run("kicad-cli", "pcb", "render", "--side", "bottom", "--rotate", "35,0,-20", "--perspective", "--quality", "high",
        "-w", "1600", "-h", "1100", "--background", "opaque", "-o", str(renders / "hub_angle.png"), str(pcb))
    copper_view(pcb, renders / "hub_copper.png")
    print("wrote", fab, renders)


def copper_view(pcb: Path, out_png: Path):
    """Both outer layers seen from the top, pours left unfilled so the
    tracks show: F.Cu (the MIPI pairs) in red, B.Cu (the parts) in blue."""
    import pcbnew
    b = pcbnew.LoadBoard(str(pcb))
    for z in b.Zones():
        z.UnFill()
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td) / pcb.name
        pcbnew.SaveBoard(str(tmp), b)
        render.plot(tmp, "F.Cu,B.Cu,Edge.Cuts", out_png, dpi=300)


if __name__ == "__main__":
    main(Path(sys.argv[1]))
