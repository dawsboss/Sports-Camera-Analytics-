#!/bin/sh
# Rebuild the hub from its netlist, the way rev A was made. This
# overwrites the KiCad files one directory up, hand edits included.
#
# Needs KiCad 9 (kicad-cli, and a Python that can import pcbnew: set
# KICAD_PYTHON if that is not python3), Java 17 or later, pdftoppm,
# Pillow, and Freerouting 1.9.0 (FREEROUTING_JAR=/path/to/
# freerouting-1.9.0.jar). Freerouting always opens a window, so a machine
# with no display needs xvfb-run (route.py says why 1.9.0).
set -e
cd "$(dirname "$0")"
PY="${KICAD_PYTHON:-python3}"
"$PY" customlib.py              # the two symbols KiCad lacks
"$PY" make_sch.py               # schematic from hub_design.py
"$PY" make_pcb.py               # outline, connectors, MIPI pairs, placement
"$PY" route.py                  # planes, ground vias, Freerouting, pours
"$PY" finish.py ../sideline-cam-hub.kicad_pcb
"$PY" fab.py ..                 # ERC, DRC with parity, fab files, renders
