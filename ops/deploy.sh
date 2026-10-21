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
  PREV_API="$(running_digest vicero-prod-api-1 "$API_REF")"; PREV_WEB="$(running_digest vicero-prod-web-1 "$WEB_REF")"
  echo "  api: $PREV_API"; echo "  web: $PREV_WEB"
  echo "$(date -u +%FT%TZ) before-deploy api=$PREV_API web=$PREV_WEB" >> "$HISTORY"

  # A pull needs about 2 GB and a migration needs room to write: a full disk with Postgres on it is an outage
  # (2026-10-20, docs/RISK-REGISTER R21). Refuse before changing anything. Override only with DEPLOY_MIN_FREE_MB.
  local FREE_BEFORE MIN_MB="${DEPLOY_MIN_FREE_MB:-3072}"
  FREE_BEFORE="$(free_mb)"
  echo "[deploy] free disk before: ${FREE_BEFORE} MB ($(used_pct)% used)"
  if [ "$FREE_BEFORE" -lt "$MIN_MB" ]; then
    echo "[deploy] REFUSED: under ${MIN_MB} MB free. Nothing was changed. Free space first (docs/25 section 18), then run again."
    return 1
  fi

  echo "[deploy] git pull --ff-only"
  git -C "$ROOT" pull --ff-only 2>&1 | tail -3

  backup_now

  echo "[deploy] pulling images (retrying, the network drops packets)"
  local i
  for i in 1 2 3 4; do
    dc pull --policy always migrate api worker beat web >/tmp/vicero-pull.log 2>&1 && break
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

  # Rebuild the read-only DB role's views and re-judge its grants after any schema change, so new columns appear and
  # new secret columns stay hidden (ops/viewer-grants.sql; no-op if the role was never created).
  bash "$ROOT/ops/viewer-setup.sh" --grants-only || echo "[deploy] WARNING: viewer grants not refreshed"

  echo "[deploy] up -d --no-build"
  dc up -d --no-build 2>&1 | tail -12

  NEW_API="$(digest_of "$API_REF")"; NEW_WEB="$(digest_of "$WEB_REF")"
  echo "$(date -u +%FT%TZ) after-deploy api=$NEW_API web=$NEW_WEB" >> "$HISTORY"
  echo "[deploy] now running: api=$NEW_API web=$NEW_WEB"
  if [ "$NEW_API" = "$PREV_API" ] && [ "$NEW_WEB" = "$PREV_WEB" ]; then
    echo "[deploy] NOTE: image digests are unchanged — nothing new was released (run the Release workflow first?)"
  fi

  echo "[deploy] free disk after: $(free_mb) MB ($(used_pct)% used; was ${FREE_BEFORE} MB)"
  if wait_healthy 600; then
    echo "[deploy] PASS"
    prune_old_images || echo "[deploy] WARNING: image cleanup failed (the deploy itself passed)"
  else
    echo "[deploy] FAIL. Roll back images with: $ROOT/rollback.sh api=$PREV_API web=$PREV_WEB"
    return 1
  fi
}

main "$@"
exit $?
