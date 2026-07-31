#!/usr/bin/env bash
#
# One-command deploy to the live server.
#
#   1. Push your changes to GitHub first (GitHub Desktop, or `git push`).
#   2. Run this from the repo root:  ./scripts/deploy.sh
#
# It SSHes to the VPS, pulls the latest main, reinstalls deps only if
# requirements.txt changed, restarts the service, and confirms the site is up.
# Your SSH key must be loaded in the agent (ssh-add --apple-use-keychain).
#
set -euo pipefail

SERVER="root@46.202.175.127"
URL="https://46.202.175.127.nip.io/login"

echo "==> Deploying to ${SERVER}"
ssh -o BatchMode=yes "$SERVER" bash -s <<'REMOTE'
set -e
cd /opt/da-marketing
before=$(sudo -u dauction git rev-parse --short HEAD)
sudo -u dauction git pull --ff-only
after=$(sudo -u dauction git rev-parse --short HEAD)
tpl_changed=0
if [ "$before" = "$after" ]; then
  echo "    no new commits (${after}) — restarting anyway"
else
  echo "    updated ${before} -> ${after}"
  changed=$(git diff --name-only "${before}" "${after}")
  # reinstall deps only when requirements.txt changed in this pull
  if echo "$changed" | grep -q '^requirements.txt$'; then
    echo "    requirements.txt changed — reinstalling deps"
    sudo -u dauction ./venv/bin/pip install -q -r requirements.txt
  fi
  # a shared partial (e.g. _brand) can change a design without touching its own
  # template file, and the thumbnail cache only tracks the direct file — so drop
  # the cache whenever anything under the render templates changed.
  if echo "$changed" | grep -q '^engine/render/templates/'; then
    tpl_changed=1
    echo "    render templates changed — clearing thumbnail cache"
    sudo -u dauction rm -f /opt/da-marketing/.ad-thumbs/*.png 2>/dev/null || true
  fi
fi
systemctl restart da-marketing
sleep 2
echo "    service: $(systemctl is-active da-marketing)"

# Pre-warm the ad-design thumbnails one at a time. Six concurrent Chromium
# launches (what the first gate-2 visitor triggers) can time out on a small
# VPS and show broken previews; sequential warming here makes them reliable.
# thumbnail() regenerates only missing/stale caches, so this is cheap on
# deploys that didn't touch a template.
echo "    warming ad thumbnails..."
sudo -u dauction PLAYWRIGHT_BROWSERS_PATH=/opt/da-marketing/.playwright \
  ./venv/bin/python - <<'WARM' || echo "    (thumbnail warming skipped)"
import os
from webapp.routes.gates import _output_root
from engine.render import ad_templates
from engine.render.ad_thumbs import thumbnail
root = _output_root(os.getenv("ENGINE_DB", "/opt/da-marketing/engine.db"))
for tid in ad_templates.template_ids():
    try:
        print("     ", tid, "ok" if thumbnail(tid, root) else "unavailable")
    except Exception as exc:
        print("     ", tid, "err", type(exc).__name__)
WARM
REMOTE

echo "==> Checking the site"
# The service takes a few seconds to accept connections after restart, so
# retry before treating a non-200 as a real failure (avoids false 502s).
code=000
for attempt in 1 2 3 4 5 6; do
  code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 12 "$URL" || echo 000)
  [ "$code" = "200" ] && break
  echo "    attempt ${attempt}: HTTP ${code} — waiting for startup..."
  sleep 2
done
echo "    ${URL} -> HTTP ${code}"
[ "$code" = "200" ] && echo "==> Deploy OK" || { echo "==> WARNING: site not returning 200"; exit 1; }
