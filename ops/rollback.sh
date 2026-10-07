#!/usr/bin/env bash
# Redeploy a specific image tag or digest, then health-check. Images only: the database moves forward only,
# so if a migration was the problem, restore from a backup instead (docs/25-PRODUCTION-RUNBOOK.md).
# Usage: rollback.sh api=<ref> [web=<ref>]     <ref> is a tag (sha-1a2b3c4, master, 1.2.0) or a digest (sha256:...)
#   e.g. rollback.sh api=sha256:abc... web=sha256:def...      (printed by deploy.sh as "previous images")
# The pin lasts until the next deploy.sh, which goes back to the moving :master tag.
set -euo pipefail
. "$(dirname "$(readlink -f "$0")")/lib.sh"

API_BASE="ghcr.io/aadesh2025/vicero-api"; WEB_BASE="ghcr.io/aadesh2025/vicero-web"
resolve() { # base ref -> full image reference
  case "$2" in
    *@sha256:*) echo "$2" ;;                       # already repo@digest
    sha256:*)   echo "$1@$2" ;;
    *)          echo "$1:$2" ;;
  esac
}
[ $# -ge 1 ] || { sed -n '2,8p' "$0"; exit 2; }
for a in "$@"; do
  case "$a" in
    api=*) export API_IMAGE="$(resolve "$API_BASE" "${a#api=}")" ;;
    web=*) export WEB_IMAGE="$(resolve "$WEB_BASE" "${a#web=}")" ;;
    *) echo "unknown argument: $a"; exit 2 ;;
  esac
done
echo "[rollback] api image: ${API_IMAGE:-unchanged (:master)}"
echo "[rollback] web image: ${WEB_IMAGE:-unchanged (:master)}"
echo "$(date -u +%FT%TZ) rollback api=${API_IMAGE:-master} web=${WEB_IMAGE:-master}" >> "$ROOT/.deploy-history"

dc pull ${API_IMAGE:+migrate api worker beat} ${WEB_IMAGE:+web} 2>&1 | tail -4
# No migrate step on purpose: an older image must not touch the schema.
dc up -d --no-build --no-deps ${API_IMAGE:+api worker beat} ${WEB_IMAGE:+web} 2>&1 | tail -8
if wait_healthy 600; then echo "[rollback] PASS"; else echo "[rollback] FAIL"; exit 1; fi
