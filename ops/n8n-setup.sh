#!/usr/bin/env bash
# One-time, idempotent preparation for the private n8n (docs/25 §11). Safe to run again: it only fills in what is
# missing. Prints variable NAMES only, never a value.
#
#   /opt/vicero/ops/n8n-setup.sh [n8n-host]          default host: viceroai-n8n.duckdns.org
#   /opt/vicero/ops/n8n-setup.sh --reset-gate        choose a new editor (basic-auth) password
#   /opt/vicero/ops/n8n-setup.sh --random-gate       unattended: random editor password into /root/n8n-editor-gate.txt
#
# What it does
#   1. Appends random secrets to ../.env when they are absent:
#        N8N_DB_PASSWORD, N8N_ENCRYPTION_KEY, N8N_WEBHOOK_SIGNING_SECRET, AUTOMATION_REPORT_SECRET
#   2. Creates the `n8n` role and database in the EXISTING Postgres, and stops that role from connecting to the
#      Vicero database at all.
#   3. Asks you (hidden prompt, twice) for the editor's second password and stores only its bcrypt hash.
# It does NOT start n8n and does NOT restart anything.
set -euo pipefail
. "$(dirname "$(readlink -f "$0")")/lib.sh"

PG_CONTAINER="${PG_CONTAINER:-vicero-prod-postgres-1}"
RESET_GATE=0
RANDOM_GATE=0
GATE_FILE="${GATE_FILE:-/root/n8n-editor-gate.txt}"
N8N_HOST="viceroai-n8n.duckdns.org"
for arg in "$@"; do
  case "$arg" in
    --reset-gate) RESET_GATE=1 ;;
    --random-gate) RANDOM_GATE=1 ;;
    *) N8N_HOST="$arg" ;;
  esac
done

has_var() { grep -qE "^$1=" "$ENV_FILE"; }
# Append NAME=value unless NAME already exists. Never echoes the value.
add_var() {
  if has_var "$1"; then echo "  kept     $1"; else printf '%s=%s\n' "$1" "$2" >> "$ENV_FILE"; echo "  created  $1"; fi
}
random_hex() { openssl rand -hex "$1"; }

[ -f "$ENV_FILE" ] || { echo "no $ENV_FILE"; exit 1; }
chmod 600 "$ENV_FILE"

echo "[n8n-setup] secrets in .env (names only)"
add_var N8N_DB_PASSWORD "$(random_hex 24)"
add_var N8N_ENCRYPTION_KEY "$(random_hex 32)"
add_var N8N_WEBHOOK_SIGNING_SECRET "$(random_hex 32)"
add_var AUTOMATION_REPORT_SECRET "$(random_hex 32)"
add_var N8N_DOMAIN "$N8N_HOST"
add_var N8N_BASE_URL "http://n8n:5678"

echo "[n8n-setup] database and role (inside the existing Postgres)"
PGUSER_="$(env_get POSTGRES_USER)"; PGUSER_="${PGUSER_:-vicero}"
PGDB_="$(env_get POSTGRES_DB)"; PGDB_="${PGDB_:-vicero}"
N8N_DB_PW="$(env_get N8N_DB_PASSWORD)"
# The password travels on stdin (a here-doc), never in a process argument.
docker exec -i "$PG_CONTAINER" psql -U "$PGUSER_" -d postgres -v ON_ERROR_STOP=1 -q <<SQL
DO \$\$ BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'n8n') THEN
    CREATE ROLE n8n LOGIN PASSWORD '${N8N_DB_PW}';
  ELSE
    ALTER ROLE n8n PASSWORD '${N8N_DB_PW}';
  END IF;
END \$\$;
SELECT 'CREATE DATABASE n8n OWNER n8n' WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'n8n')\gexec
-- n8n may use its own database and nothing else: no connecting to the Vicero database.
REVOKE CONNECT ON DATABASE "${PGDB_}" FROM PUBLIC;
GRANT CONNECT ON DATABASE "${PGDB_}" TO "${PGUSER_}";
REVOKE ALL ON DATABASE postgres FROM n8n;
SQL
echo "  role n8n + database n8n ready; role n8n cannot connect to the ${PGDB_} database"

if has_var N8N_EDITOR_GATE_HASH && [ "$RESET_GATE" = 0 ]; then
  echo "[n8n-setup] editor gate: kept (use --reset-gate to change the password)"
else
  echo "[n8n-setup] editor gate (a second password in front of the n8n login)"
  if [ "$RANDOM_GATE" = 1 ]; then
    # Unattended: a random password goes to a root-only file (never printed); read it once, then delete the file.
    gate_user="ops"; gate_pw1="$(random_hex 16)"; gate_pw2="$gate_pw1"
    (umask 077; printf 'user: %s\npassword: %s\n' "$gate_user" "$gate_pw1" > "$GATE_FILE")
    echo "  random editor password written to $GATE_FILE (root only, not shown here)"
  else
    read -r -p "  editor user name: " gate_user
    read -r -s -p "  password (hidden): " gate_pw1; echo
    read -r -s -p "  again: " gate_pw2; echo
  fi
  [ -n "$gate_user" ] && [ -n "$gate_pw1" ] && [ "$gate_pw1" = "$gate_pw2" ] || { echo "empty or not matching; nothing changed"; exit 1; }
  # Hashed by Caddy itself. The plaintext is visible to root in the process list for a moment, and nowhere else.
  gate_hash="$(docker run --rm caddy:2 caddy hash-password --plaintext "$gate_pw1")"
  unset gate_pw1 gate_pw2
  tmp="$(mktemp)"; grep -vE '^N8N_EDITOR_GATE_(USER|HASH)=' "$ENV_FILE" > "$tmp"
  # Single quotes: Compose must not read the `$` in a bcrypt hash as variables.
  { printf "N8N_EDITOR_GATE_USER=%s\n" "$gate_user"; printf "N8N_EDITOR_GATE_HASH='%s'\n" "$gate_hash"; } >> "$tmp"
  cat "$tmp" > "$ENV_FILE"; rm -f "$tmp"; chmod 600 "$ENV_FILE"
  echo "  set      N8N_EDITOR_GATE_USER, N8N_EDITOR_GATE_HASH"
fi

cat <<'MSG'

[n8n-setup] done. Nothing was restarted.
Next (docs/25 §11):
  1. Save N8N_ENCRYPTION_KEY in your password manager. To read it once, on the server:
        grep '^N8N_ENCRYPTION_KEY=' /opt/vicero/.env
     Losing it makes every credential saved inside n8n unreadable.
  2. Deploy (asks before restarting):  /opt/vicero/deploy.sh
  3. Open https://<n8n-host>, pass the editor gate, create the n8n OWNER account (you type its password),
     then Settings -> n8n API -> create an API key and add it with:  /opt/vicero/set-env.sh N8N_API_KEY
MSG
