#!/usr/bin/env bash
# Set ONE value in /opt/vicero/.env with a hidden prompt, keep chmod 600, restart only what uses it.
# Usage: /opt/vicero/set-env.sh KEY        (the value is typed at a silent prompt and never echoed or logged)
set -euo pipefail
. "$(dirname "$(readlink -f "$0")")/lib.sh"

KEY="${1:-}"
[[ "$KEY" =~ ^[A-Z][A-Z0-9_]*$ ]] || { echo "usage: $0 KEY   (UPPER_SNAKE_CASE)"; exit 2; }

# Which services read this value. Containers read ../.env only at creation, so they must be recreated.
case "$KEY" in
  NEXT_PUBLIC_*)
    echo "NOTE: $KEY is baked into the web image at build time. Setting it here changes nothing live."
    echo "      Set the GitHub repo variable, re-run Actions -> Release, then deploy.sh."; SERVICES="" ;;
  POSTGRES_PASSWORD|POSTGRES_USER|POSTGRES_DB)
    echo "REFUSED: changing $KEY in .env does NOT change the existing database's credentials and would"
    echo "         lock every service out. Change it inside Postgres first (ALTER USER), then ask for help."; exit 3 ;;
  DOMAIN|API_DOMAIN|ACME_EMAIL) SERVICES="caddy" ;;
  *_MEM_LIMIT|*_MEM_RESERVATION) SERVICES="ALL" ;;
  SECRET_KEY)
    echo "NOTE: rotating SECRET_KEY signs every user out and invalidates stored sessions/tokens."; SERVICES="api worker beat" ;;
  *) SERVICES="api worker beat" ;;
esac

read -r -s -p "New value for $KEY (hidden): " VAL; echo
[ -n "$VAL" ] || { echo "empty value, nothing changed"; exit 2; }

TMP="$(umask 077; mktemp "$ENV_FILE.XXXXXX")"
trap 'rm -f "$TMP"; unset VAL' EXIT
VAL="$VAL" KEY="$KEY" python3 - "$ENV_FILE" "$TMP" <<'PY'
import os, sys
src, dst = sys.argv[1], sys.argv[2]
key, val = os.environ["KEY"], os.environ["VAL"]
out, done = [], False
for line in open(src):
    if line.startswith(key + "="):
        out.append(f"{key}={val}\n"); done = True
    else:
        out.append(line)
if not done:
    out.append(f"{key}={val}\n")
open(dst, "w").write("".join(out))
PY
chmod 600 "$TMP"; mv "$TMP" "$ENV_FILE"; chmod 600 "$ENV_FILE"; trap - EXIT; unset VAL
echo "$KEY updated in .env (value not shown). Names now set: $(grep -c '=' "$ENV_FILE") entries."

if [ "$SERVICES" = "ALL" ]; then
  echo "recreating changed services"; dc up -d --no-build 2>&1 | tail -8
elif [ -n "$SERVICES" ]; then
  echo "recreating: $SERVICES"; dc up -d --no-build --no-deps --force-recreate $SERVICES 2>&1 | tail -8
fi
[ -n "$SERVICES" ] && wait_healthy 600 || true
