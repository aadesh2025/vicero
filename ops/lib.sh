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

# Image digest currently behind a reference, or "none" (used for the rollback trail).
digest_of() { docker image inspect --format '{{index .RepoDigests 0}}' "$1" 2>/dev/null || echo "none"; }

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
