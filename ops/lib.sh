#!/usr/bin/env bash
# Shared helpers for the ops scripts. Sourced, never run directly. No secret is ever read or printed here.
ROOT="${VICERO_ROOT:-/opt/vicero}"
INFRA="$ROOT/infra"
ENV_FILE="$ROOT/.env"

# NOTE: --env-file is required; `${VAR}` interpolation in the compose file does not read ../.env otherwise.
dc() {
  (cd "$INFRA" && docker compose --env-file ../.env -f docker-compose.prod.yml -f docker-compose.ghcr.yml "$@")
}

# Non-secret .env values only (domains). Never call with a secret name.
env_get() { grep -E "^$1=" "$ENV_FILE" | head -1 | cut -d= -f2-; }

# Free space on / in MB, and use in percent (the root disk holds Docker's images, Postgres and the backups).
free_mb() { df -BM --output=avail / | tail -1 | tr -dc '0-9'; }
used_pct() { df --output=pcent / | tail -1 | tr -dc '0-9'; }

# Image digest currently behind a reference, or "none" (used for the rollback trail).
digest_of() { docker image inspect --format '{{index .RepoDigests 0}}' "$1" 2>/dev/null || echo "none"; }

# Digest of the image a RUNNING container was started from (the true "previous version"), falling back to the
# local tag's digest when the container is absent. The tag can move on without the container (a manual pull),
# so reading the tag alone can record the wrong rollback target.
running_digest() { # <container> <fallback-ref>
  local id
  id="$(docker inspect --format '{{.Image}}' "$1" 2>/dev/null || true)"
  if [ -n "$id" ]; then docker image inspect --format '{{index .RepoDigests 0}}' "$id" 2>/dev/null && return 0; fi
  digest_of "$2"
}

# After a good deploy: remove old untagged images (each release leaves a 2 GB pair behind, RISK-REGISTER R21).
# Keeps every image a container is using, the :master tags, and the digests of the last KEEP_RELEASE_PAIRS release
# pairs in .deploy-history (default 3 = the running pair + the 2 before it, so rollback.sh still has targets).
# Images only: never a volume. PRUNE_DRY_RUN=1 prints what it would remove and removes nothing.
prune_old_images() {
  local pairs="${KEEP_RELEASE_PAIRS:-3}" dry="${PRUNE_DRY_RUN:-0}" keep id size n=0 before
  before="$(free_mb)"
  keep="$( { docker ps -q | xargs -r docker inspect --format '{{.Image}}'
             [ -f "$ROOT/.deploy-history" ] && grep -o 'sha256:[0-9a-f]\{64\}' "$ROOT/.deploy-history" | tac | awk '!seen[$0]++' | grep -Fxf <(docker images -a --no-trunc --format '{{.ID}}') | head -n $((pairs * 2))
           } | sort -u )" || true
  echo "[prune] keeping $(echo "$keep" | grep -c .) image(s): running + last $pairs release pairs"
  while IFS='|' read -r id size; do
    [ -n "$id" ] || continue
    if echo "$keep" | grep -qx "$id"; then continue; fi
    if [ "$dry" = 1 ]; then echo "[prune] would remove ${id:0:19} ($size)"; n=$((n + 1)); continue; fi
    if docker rmi "$id" >/dev/null 2>&1; then echo "[prune] removed ${id:0:19} ($size)"; n=$((n + 1)); else echo "[prune] kept ${id:0:19}: in use"; fi
  done < <(docker images -a --no-trunc --filter dangling=true --format '{{.ID}}|{{.Size}}')
  echo "[prune] $([ "$dry" = 1 ] && echo "would remove" || echo "removed") $n image(s); free disk now $(free_mb) MB ($(used_pct)% used; was ${before} MB)"
}

# Wait until api + web report healthy, caddy runs, and both HTTPS endpoints answer. PASS/FAIL, exit 0/1.
wait_healthy() {
  local timeout="${1:-600}" api_domain domain start=$SECONDS
  api_domain="$(env_get API_DOMAIN)"; domain="$(env_get DOMAIN)"
  while :; do
    local api web caddy a w
    api="$(docker inspect -f '{{.State.Health.Status}}' vicero-prod-api-1 2>/dev/null || echo missing)"
    web="$(docker inspect -f '{{.State.Health.Status}}' vicero-prod-web-1 2>/dev/null || echo missing)"
    caddy="$(docker inspect -f '{{.State.Status}}' vicero-prod-caddy-1 2>/dev/null || echo missing)"
    a="$(curl -sS -m 10 -o /dev/null -w '%{http_code}' "https://$api_domain/readyz" 2>/dev/null || echo 000)"
    w="$(curl -sS -m 10 -o /dev/null -w '%{http_code}' "https://$domain/login" 2>/dev/null || echo 000)"
    if [ "$api" = healthy ] && [ "$web" = healthy ] && [ "$caddy" = running ] && [ "$a" = 200 ] && [ "$w" = 200 ]; then
      echo "[health] PASS  api=$api web=$web caddy=$caddy readyz=$a login=$w ($((SECONDS - start))s)"
      return 0
    fi
    if [ $((SECONDS - start)) -ge "$timeout" ]; then
      echo "[health] FAIL  api=$api web=$web caddy=$caddy readyz=$a login=$w after ${timeout}s"
      return 1
    fi
    sleep 5
  done
}

# Fresh DB dump + uploads archive via the running backup container. Aborts the caller on failure.
backup_now() {
  echo "[backup] taking a fresh backup first"
  docker exec vicero-prod-backup-1 sh /scripts/backup.sh 2>&1 | grep -E 'wrote|done|ERROR|WARNING' || true
  docker exec vicero-prod-backup-1 sh -c 'ls -t /backups/vicero_vicero_*.sql.gz | head -1' | sed 's/^/[backup] newest dump: /'
}
