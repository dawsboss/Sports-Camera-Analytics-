"""Plot chosen layers of the hub board to a cropped PNG for review.

    python render.py <board> <layers> <out.png> [dpi]
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageOps


def plot(pcb, layers, out_png, dpi=300, mirror=False):
    with tempfile.TemporaryDirectory() as td:
        pdf = Path(td) / "p.pdf"
        cmd = ["kicad-cli", "pcb", "export", "pdf", "--layers", layers, "--mode-single", "-o", str(pdf), str(pcb)]
        if mirror:
            cmd.insert(4, "--mirror")
        subprocess.run(cmd, check=True, capture_output=True)
        subprocess.run(["pdftoppm", "-png", "-r", str(dpi), "-singlefile", str(pdf), str(Path(td) / "p")], check=True)
        im = Image.open(Path(td) / "p.png").convert("RGB")
        bbox = ImageOps.invert(im).getbbox()
        if bbox:
            pad = 20
            im = im.crop((max(0, bbox[0] - pad), max(0, bbox[1] - pad),
                          min(im.width, bbox[2] + pad), min(im.height, bbox[3] + pad)))
        im.save(out_png)
    return out_png


if __name__ == "__main__":
    print(plot(sys.argv[1], sys.argv[2], sys.argv[3], dpi=int(sys.argv[4]) if len(sys.argv) > 4 else 300))
