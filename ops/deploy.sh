#!/usr/bin/env bash
# Deploy the latest code + images: pull repo, back up, pull images, migrate (forward only), restart, health-check.
# Usage on the server: /opt/vicero/deploy.sh
set -euo pipefail
. "$(dirname "$(readlink -f "$0")")/lib.sh"

API_REF="ghcr.io/aadesh2025/vicero-api:master"
WEB_REF="ghcr.io/aadesh2025/vicero-web:master"
HISTORY="$ROOT/.deploy-history"

# Everything lives in main() and is called on the last line: bash parses the whole function before running
# it, so the `git pull` below can safely rewrite this very file mid-run.
main() {
  echo "[deploy] previous images (keep these for rollback.sh):"
  local PREV_API PREV_WEB NEW_API NEW_WEB
  PREV_API="$(digest_of "$API_REF")"; PREV_WEB="$(digest_of "$WEB_REF")"
  echo "  api: $PREV_API"; echo "  web: $PREV_WEB"
  echo "$(date -u +%FT%TZ) before-deploy api=$PREV_API web=$PREV_WEB" >> "$HISTORY"

  echo "[deploy] git pull --ff-only"
  git -C "$ROOT" pull --ff-only 2>&1 | tail -3

  backup_now

  echo "[deploy] pulling images (retrying, the network drops packets)"
  local i
  for i in 1 2 3 4; do
    dc pull migrate api worker beat web >/tmp/vicero-pull.log 2>&1 && break
    echo "  pull attempt $i failed, retrying"; sleep 5
    if [ "$i" = 4 ]; then tail -5 /tmp/vicero-pull.log; echo "[deploy] FAIL: could not pull images"; return 1; fi
  done

  echo "[deploy] migrations (alembic upgrade head, never downgrade)"
  if ! dc up --no-build --no-deps --exit-code-from migrate migrate >/tmp/vicero-migrate.log 2>&1; then
    tail -15 /tmp/vicero-migrate.log
    echo "[deploy] FAIL: migration failed, nothing was restarted. The backup above is the restore point."
    return 1
  fi
  echo "[deploy] migrations OK"

  echo "[deploy] up -d --no-build"
  dc up -d --no-build 2>&1 | tail -12

  NEW_API="$(digest_of "$API_REF")"; NEW_WEB="$(digest_of "$WEB_REF")"
  echo "$(date -u +%FT%TZ) after-deploy api=$NEW_API web=$NEW_WEB" >> "$HISTORY"
  echo "[deploy] now running: api=$NEW_API web=$NEW_WEB"

  if wait_healthy 600; then
    echo "[deploy] PASS"
  else
    echo "[deploy] FAIL. Roll back images with: $ROOT/rollback.sh api=$PREV_API web=$PREV_WEB"
    return 1
  fi
}

main "$@"
exit $?
