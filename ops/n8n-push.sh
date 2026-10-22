#!/usr/bin/env bash
# Run scripts/n8n-push.mjs on the VPS, inside the existing web container (it has Node; the host does not).
# The key is read from .env here and handed to the container by NAME only (`-e N8N_API_KEY`), so it is never in a
# process argument, a log or the output. Nothing is pulled or built.
#
#   /opt/vicero/ops/n8n-push.sh push - --org ... < infra/n8n/clients/<slug>/<agent>-<job>.json     (dry run)
#   /opt/vicero/ops/n8n-push.sh push - --org ... --apply < ...json
#   ssh -t vicero /opt/vicero/ops/n8n-push.sh activate "<Client - Agent - Job>" --org <slug>        (a person only)
#
# Manifests and history.log live in /opt/vicero/n8n-push-state (outside git, root only); they are copied into the
# container before each run and back out after it.
set -euo pipefail
. "$(dirname "$(readlink -f "$0")")/lib.sh"

C="${N8N_PUSH_CONTAINER:-vicero-prod-web-1}"
STATE="$ROOT/n8n-push-state"
IN_STATE=/tmp/n8n-push-state

[ "${1:-}" ] || { echo "usage: $0 push <file|-> <flags> | activate <name> --org <slug>"; exit 2; }
if [ "$1" = activate ] && { [ ! -t 0 ] || [ ! -t 1 ]; }; then
  echo "REFUSED: activate needs a person at a real terminal. Run: ssh -t vicero $0 activate ..."; exit 3
fi

# Read the key without printing it. Not env_get: lib.sh says never to call that with a secret name.
N8N_API_KEY="$(grep -m1 '^N8N_API_KEY=' "$ENV_FILE" | cut -d= -f2-)"
[ -n "$N8N_API_KEY" ] || { echo "N8N_API_KEY is not set in $ENV_FILE (mint one in the n8n editor, then set-env.sh N8N_API_KEY)"; exit 1; }
export N8N_API_KEY
export N8N_BASE_URL="${N8N_BASE_URL_OVERRIDE:-http://n8n:5678}"

mkdir -p "$STATE"; chmod 700 "$STATE"
docker cp "$ROOT/scripts/n8n-push.mjs" "$C:/tmp/n8n-push.mjs"
docker exec -u root "$C" mkdir -p "$IN_STATE"
[ -z "$(ls -A "$STATE")" ] || docker cp "$STATE/." "$C:$IN_STATE"
docker exec -u root "$C" chown -R node:node "$IN_STATE" /tmp/n8n-push.mjs

FLAGS=(-i)
{ [ -t 0 ] && [ -t 1 ]; } && FLAGS=(-it)
rc=0
docker exec "${FLAGS[@]}" -e N8N_API_KEY -e N8N_BASE_URL -e "N8N_PUSH_STATE_DIR=$IN_STATE" "$C" node /tmp/n8n-push.mjs "$@" || rc=$?

docker cp "$C:$IN_STATE/." "$STATE" 2>/dev/null || true
chmod -R go-rwx "$STATE"
docker exec -u root "$C" rm -f /tmp/n8n-push.mjs
exit "$rc"
