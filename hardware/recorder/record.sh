#!/usr/bin/env bash
# Record the head's four main streams on the base-station mini PC.
#
# Stream copy, never a re-encode: the files are exactly what the cameras
# sent. Each camera's stream is cut into ten-minute segments that start on
# the same clock boundaries, so the four views line up by file name, and S0
# stitches a camera's segments at ingest (docs/SPEC.md, input contract).
# The cameras take their time from this machine (chrony-sideline.conf), so
# names and stream clocks share one clock. Raw video stays on this disk
# until it is copied to the homelab's MinIO; it goes nowhere else. Video
# only: S0 drops audio anyway, and a microphone on a sideline records the
# parents standing under it.
#
#   CAM_AUTH=admin:password ./record.sh            # Ctrl-C to stop
#   PATH_MAIN=h265Preview_01_main CAM_AUTH=admin:password ./record.sh   # the pod
set -euo pipefail

: "${CAM_AUTH:?set CAM_AUTH=user:password for the cameras}"
OUT=${OUT:-/data/rec/$(date +%Y%m%d-%H%M)}
PATH_MAIN=${PATH_MAIN:-main}          # Milesight main stream; the pod's Reolinks: h265Preview_01_main
declare -A CAMS=(
  [far-l]=192.168.50.11
  [far-r]=192.168.50.12
  [near-l]=192.168.50.13
  [near-r]=192.168.50.14
)

mkdir -p "$OUT"
trap 'kill 0' INT TERM
for name in "${!CAMS[@]}"; do
  ffmpeg -nostdin -loglevel warning -rtsp_transport tcp \
    -i "rtsp://${CAM_AUTH}@${CAMS[$name]}:554/${PATH_MAIN}" -map 0:v -c copy \
    -f segment -segment_time 600 -segment_atclocktime 1 -reset_timestamps 1 -strftime 1 \
    -segment_list "$OUT/${name}_segments.csv" -segment_list_type csv \
    "$OUT/${name}_%Y%m%d-%H%M%S.mkv" &
done
echo "recording ${#CAMS[@]} cameras to $OUT"
wait
