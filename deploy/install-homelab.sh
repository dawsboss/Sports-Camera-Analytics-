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
set -euo pipefail

REPO="${SIDELINE_REPO:-https://github.com/dawsboss/Sports-Camera-Analytics-.git}"
BRANCH="${SIDELINE_BRANCH:-main}"
ROOT=/srv/sideline
PORT="${SIDELINE_PORT:-8000}"

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
"$ROOT/venv/bin/pip" install -q -e "$ROOT/app[service]"

chown -R sideline:sideline "$ROOT"

sed "s/--port 8000/--port $PORT/" "$ROOT/app/deploy/sideline.service" > /etc/systemd/system/sideline.service
systemctl daemon-reload
systemctl enable -q sideline
systemctl restart sideline

sleep 2
if ! systemctl is-active -q sideline; then
  echo "sideline did not start:" >&2
  journalctl -u sideline -n 30 --no-pager >&2
  exit 1
fi

HOST_IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
echo
echo "Sideline is serving on this network."
echo "  health:  http://${HOST_IP}:${PORT}/health"
echo "  review:  http://${HOST_IP}:${PORT}/review?match=<match id>"
echo "  docs:    http://${HOST_IP}:${PORT}/docs"
echo "Matches go in ${ROOT}/data (the layout 'sideline ingest --data' writes)."
echo "Logs: journalctl -u sideline -f"
