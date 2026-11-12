#!/usr/bin/env bash
# Put the Sideline API and review page on this machine's home network.
#
# Debian/Ubuntu, run as root (a Proxmox LXC or VM). Idempotent: run it again
# after `git pull` to update. It makes a `sideline` user, a checkout under
# /srv/sideline/app, a virtualenv, and a systemd service bound to every
# interface on port 8000. Matches live in /srv/sideline/data, the same layout
# `sideline ingest --data` writes, so a run from another machine arrives with
#
#   rsync -a --delete ./data/ sideline@<this host>:/srv/sideline/data/
#
# The service serves sampled frames to this network. Do not forward the port
# to the internet or put it behind a public tunnel; use Tailscale from outside.
#
# With SIDELINE_GPU=1 it also installs the worker (deploy/sideline-worker.service):
# the API then only queues jobs and the worker runs them, holding the detector
# and the GPU. That needs the NVIDIA driver on the host and the card's devices
# handed to this container (see README, "The GPU worker"); the script installs
# the matching torch build (SIDELINE_TORCH_INDEX, default the CUDA 12.6 wheels,
# the last to support a Pascal card such as a GTX 1080), the detector, and
# downloads the COCO weights once.
set -euo pipefail

REPO="${SIDELINE_REPO:-https://github.com/dawsboss/Sports-Camera-Analytics-.git}"
BRANCH="${SIDELINE_BRANCH:-main}"
ROOT=/srv/sideline
PORT="${SIDELINE_PORT:-8000}"
GPU="${SIDELINE_GPU:-0}"
TORCH_INDEX="${SIDELINE_TORCH_INDEX:-https://download.pytorch.org/whl/cu126}"
WEIGHTS="${SIDELINE_WEIGHTS:-yolo11x.pt}"

if [ "$(id -u)" -ne 0 ]; then echo "run as root" >&2; exit 1; fi

apt-get update -qq
apt-get install -y -qq --no-install-recommends git python3 python3-venv python3-pip ffmpeg rsync >/dev/null

id -u sideline >/dev/null 2>&1 || useradd --system --home "$ROOT" --shell /usr/sbin/nologin sideline
mkdir -p "$ROOT/data"

if [ -d "$ROOT/app/.git" ]; then
  git -C "$ROOT/app" fetch -q origin "$BRANCH"
  git -C "$ROOT/app" checkout -q "$BRANCH"
  git -C "$ROOT/app" pull -q --ff-only origin "$BRANCH"
else
  git clone -q --branch "$BRANCH" "$REPO" "$ROOT/app"
fi

[ -x "$ROOT/venv/bin/python" ] || python3 -m venv "$ROOT/venv"
"$ROOT/venv/bin/pip" install -q --upgrade pip
if [ "$GPU" = "1" ]; then
  "$ROOT/venv/bin/pip" install -q torch torchvision --index-url "$TORCH_INDEX"
  "$ROOT/venv/bin/pip" install -q -e "$ROOT/app[service,gpu]"
else
  "$ROOT/venv/bin/pip" install -q -e "$ROOT/app[service]"
fi

mkdir -p "$ROOT/data/work" "$ROOT/data/home" "$ROOT/data/models"
if [ "$GPU" = "1" ] && [ ! -f "$ROOT/data/models/$WEIGHTS" ]; then
  # Ultralytics fetches a named checkpoint from its releases; keep it where
  # the worker may write, and only once.
  (cd "$ROOT/data/models" && HOME="$ROOT/data/home" "$ROOT/venv/bin/python" -c "from ultralytics import YOLO; YOLO('$WEIGHTS')")
fi
chown -R sideline:sideline "$ROOT"

sed "s/--port 8000/--port $PORT/" "$ROOT/app/deploy/sideline.service" > /etc/systemd/system/sideline.service
mkdir -p /etc/systemd/system/sideline.service.d
if [ "$GPU" = "1" ]; then
  # The API queues; the worker runs. Without a worker the thread runner stays.
  printf '[Service]\nEnvironment=SIDELINE_JOB_RUNNER=db\n' > /etc/systemd/system/sideline.service.d/runner.conf
  sed "s#yolo11x.pt#$WEIGHTS#" "$ROOT/app/deploy/sideline-worker.service" > /etc/systemd/system/sideline-worker.service
  systemctl enable -q sideline-worker
else
  rm -f /etc/systemd/system/sideline.service.d/runner.conf
fi
systemctl daemon-reload
systemctl enable -q sideline
systemctl restart sideline
if [ "$GPU" = "1" ]; then
  systemctl restart sideline-worker
fi

sleep 2
if ! systemctl is-active -q sideline; then
  echo "sideline did not start:" >&2
  journalctl -u sideline -n 30 --no-pager >&2
  exit 1
fi

if [ "$GPU" = "1" ]; then
  sleep 3
  if ! systemctl is-active -q sideline-worker; then
    echo "sideline-worker did not start:" >&2
    journalctl -u sideline-worker -n 30 --no-pager >&2
    exit 1
  fi
  echo
  echo "GPU as the worker sees it:"
  # A minimal container has no sudo; runuser is in util-linux everywhere.
  runuser -u sideline -- env HOME="$ROOT/data/home" "$ROOT/venv/bin/python" -c "import torch; print('  cuda available:', torch.cuda.is_available()); print('  device:', torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'none; the worker will run on the CPU, slowly')"
fi

HOST_IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
echo
echo "Sideline is serving on this network."
echo "  health:  http://${HOST_IP}:${PORT}/health"
echo "  review:  http://${HOST_IP}:${PORT}/review?match=<match id>"
echo "  docs:    http://${HOST_IP}:${PORT}/docs"
echo "Matches go in ${ROOT}/data (the layout 'sideline ingest --data' writes)."
echo "Logs: journalctl -u sideline -f"
