#!/usr/bin/env bash
# Read-only Postgres role for a visual client (DBeaver) reached over an SSH tunnel (docs/25 §13). Idempotent.
#
#   /opt/vicero/ops/viewer-setup.sh                create the role (prints its password ONCE), apply grants
#   /opt/vicero/ops/viewer-setup.sh --rotate       new password, shown once, same grants
#   /opt/vicero/ops/viewer-setup.sh --grants-only  re-apply grants only; the password is untouched (deploy.sh runs this)
#   /opt/vicero/ops/viewer-setup.sh --drop         remove the role
#
# What `viewer` can do: SELECT. Nothing else (no write, no DDL, read-only transactions, 30 s statement limit,
# 5 connections). Secrets are kept out of reach:
#   * public tables with NO secret column are readable as they are;
#   * public tables WITH a secret column (hash / encrypted / token / secret columns, plus the columns named in
#     viewer-grants.sql `hidden_pairs`) are NOT
#     granted; the `viewer` schema holds a same-named view with only the safe columns, and it comes first in the
#     role's search_path;
#   * token tables and `sessions` are not exposed at all.
# The password is never written to a file, .env or git; it goes to psql on stdin and is printed once on create/rotate.
set -euo pipefail
. "$(dirname "$(readlink -f "$0")")/lib.sh"

PG_CONTAINER="${PG_CONTAINER:-vicero-prod-postgres-1}"
MODE=create
for arg in "$@"; do
  case "$arg" in
    --rotate) MODE=rotate ;;
    --grants-only) MODE=grants ;;
    --drop) MODE=drop ;;
    *) echo "unknown option: $arg"; exit 2 ;;
  esac
done

PGUSER_="$(env_get POSTGRES_USER)"; PGUSER_="${PGUSER_:-vicero}"
PGDB_="$(env_get POSTGRES_DB)"; PGDB_="${PGDB_:-vicero}"
psql_() { docker exec -i "$PG_CONTAINER" psql -U "$PGUSER_" -d "$1" -v ON_ERROR_STOP=1 -q -X; }

role_exists() {
  [ "$(docker exec -i "$PG_CONTAINER" psql -U "$PGUSER_" -d postgres -tAc "SELECT 1 FROM pg_roles WHERE rolname='viewer'")" = 1 ]
}

if [ "$MODE" = drop ]; then
  psql_ "$PGDB_" <<SQL
DROP SCHEMA IF EXISTS viewer CASCADE;
DROP OWNED BY viewer;
DROP ROLE IF EXISTS viewer;
SQL
  echo "[viewer] role and schema removed"
  exit 0
fi

if [ "$MODE" = grants ] && ! role_exists; then
  echo "[viewer] no viewer role on this server: nothing to do"
  exit 0
fi

PW=""
if [ "$MODE" = create ] || [ "$MODE" = rotate ]; then
  if [ "$MODE" = create ] && role_exists; then
    echo "[viewer] role already exists (grants refreshed, password unchanged). Use --rotate for a new password."
    MODE=grants
  else
    # Hex only, so it needs no quoting anywhere (SQL, DBeaver, shell).
    PW="$(openssl rand -hex 18)"
    psql_ postgres <<SQL
DO \$\$ BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'viewer') THEN
    CREATE ROLE viewer LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS CONNECTION LIMIT 5 PASSWORD '${PW}';
  ELSE
    ALTER ROLE viewer LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS CONNECTION LIMIT 5 PASSWORD '${PW}';
  END IF;
END \$\$;
SQL
  fi
fi

# Grants. `REVOKE CONNECT ... FROM PUBLIC` was run by n8n-setup.sh, so CONNECT must be granted explicitly.
# The rest (settings, schema, hidden columns, the rebuilt views) is ops/viewer-grants.sql, shared with the CI test.
SQL_FILE="$(dirname "$(readlink -f "$0")")/viewer-grants.sql"
{ echo "GRANT CONNECT ON DATABASE \"${PGDB_}\" TO viewer;"; cat "$SQL_FILE"; } | psql_ "$PGDB_"

echo "[viewer] grants applied (SELECT only; secret columns and token tables excluded)"
if [ -n "$PW" ]; then
  echo
  echo "  user:     viewer"
  echo "  password: $PW"
  echo "  This is the only time it is shown. It is not stored anywhere on the server."
fi
