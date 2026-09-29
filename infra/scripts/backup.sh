#!/usr/bin/env bash
# Vicero backup — pg_dump (DB) + tar (uploads volume), both timestamped/compressed, with
# shared rotation. See ADR-082: restoring the DB dump alone used to leave every `documents`
# row pointing at a storage_path/docling_json_path that no longer existed on disk
# (RISK-REGISTER R1, docs/15 §8.3) — the uploads archive is what fixes that.
#
# Cron example (daily 03:15, keep 14 days):
#   15 3 * * *  BACKUP_DIR=/var/backups/vicero RETENTION_DAYS=14 UPLOADS_DIR=/mnt/uploads /opt/vicero/infra/scripts/backup.sh
#
# In Docker, run it from a container that can reach postgres AND has the `uploads` volume
# mounted read-only, e.g.:
#   docker compose exec -T backup sh /scripts/backup.sh
# The prod compose (docker-compose.prod.yml) ships exactly this as the `backup` service.
set -euo pipefail

BACKUP_DIR="${BACKUP_DIR:-./backups}"
RETENTION_DAYS="${RETENTION_DAYS:-14}"
PGHOST="${PGHOST:-localhost}"
PGPORT="${PGPORT:-5432}"
PGUSER="${PGUSER:-${POSTGRES_USER:-vicero}}"
PGDATABASE="${PGDATABASE:-${POSTGRES_DB:-vicero}}"
export PGPASSWORD="${PGPASSWORD:-${POSTGRES_PASSWORD:-vicero}}"
# Where the uploads volume is mounted INSIDE this backup container/host (read-only), e.g.
# /uploads. Empty by default — deliberately: a caller that only ever had DB access (a bare
# Postgres host, someone's dev machine) must keep working exactly as before, not start failing
# because it doesn't have this mount. A missing mount is a loud warning, not a hard error, for
# the same reason: this script must never let a DB-only environment look broken.
UPLOADS_DIR="${UPLOADS_DIR:-}"

mkdir -p "$BACKUP_DIR"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
DB_OUT="$BACKUP_DIR/vicero_${PGDATABASE}_${STAMP}.sql.gz"

echo "[backup] pg_dump $PGDATABASE@$PGHOST:$PGPORT -> $DB_OUT"
# Custom-format-free plain SQL, gzipped — restore with restore.sh.
pg_dump --host="$PGHOST" --port="$PGPORT" --username="$PGUSER" \
        --no-owner --no-privileges "$PGDATABASE" | gzip -9 > "$DB_OUT"
echo "[backup] wrote $(du -h "$DB_OUT" | cut -f1)"

if [ -n "$UPLOADS_DIR" ]; then
  if [ -d "$UPLOADS_DIR" ]; then
    UPLOADS_OUT="$BACKUP_DIR/vicero_uploads_${STAMP}.tar.gz"
    echo "[backup] archiving uploads $UPLOADS_DIR -> $UPLOADS_OUT"
    # -C "$UPLOADS_DIR" so the archive holds paths relative to the mount, not the mount's own
    # absolute path — portable to a restore target with a different mount point.
    tar -C "$UPLOADS_DIR" -czf "$UPLOADS_OUT" .
    echo "[backup] wrote $(du -h "$UPLOADS_OUT" | cut -f1)"
  else
    echo "[backup] WARNING: UPLOADS_DIR=$UPLOADS_DIR is set but does not exist — uploads NOT backed up this run" >&2
  fi
else
  echo "[backup] WARNING: UPLOADS_DIR not set — uploads volume NOT backed up (DB dump only). See docs/09 §4 / ADR-082." >&2
fi

# Rotate: delete backups (both DB dumps and uploads archives) older than RETENTION_DAYS.
find "$BACKUP_DIR" \( -name 'vicero_*.sql.gz' -o -name 'vicero_uploads_*.tar.gz' \) \
     -type f -mtime "+${RETENTION_DAYS}" -print -delete || true
echo "[backup] done; retention ${RETENTION_DAYS}d"
