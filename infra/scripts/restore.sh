#!/usr/bin/env bash
# Vicero restore — load a gzipped pg_dump produced by backup.sh, and optionally its matching
# uploads archive (ADR-082).
#
#   ./restore.sh vicero_vicero_20260824T031500Z.sql.gz
#   ./restore.sh vicero_vicero_20260824T031500Z.sql.gz vicero_uploads_20260824T031500Z.tar.gz
#
# WARNING: this restores INTO the target database and (if given) the target uploads directory.
# For a clean DB restore, drop+recreate the DB first (the commented block below); migrations are
# NOT needed — the dump is a full schema+data snapshot.
#
# The uploads archive is optional and backward compatible: omit it and this behaves exactly as
# it always has. But without it, every restored `documents` row's `storage_path` and
# `docling_json_path` point at a file that was never restored (docs/15 §8.3) — pass the archive
# from the SAME backup run (same timestamp) whenever one exists, which it does for every backup
# taken after ADR-082 shipped.
set -euo pipefail

DUMP="${1:?usage: restore.sh <backup.sql.gz> [uploads.tar.gz]}"
UPLOADS_ARCHIVE="${2:-}"
PGHOST="${PGHOST:-localhost}"
PGPORT="${PGPORT:-5432}"
PGUSER="${PGUSER:-${POSTGRES_USER:-vicero}}"
PGDATABASE="${PGDATABASE:-${POSTGRES_DB:-vicero}}"
export PGPASSWORD="${PGPASSWORD:-${POSTGRES_PASSWORD:-vicero}}"
# Where to extract the uploads archive TO — must match the UPLOAD_DIR the api/worker containers
# actually read (docker-compose.prod.yml's `uploads` volume mount, /app/var/uploads). No default:
# guessing wrong here silently restores documents nobody can find, so it must be explicit.
UPLOADS_TARGET_DIR="${UPLOADS_TARGET_DIR:-}"

echo "[restore] restoring $DUMP -> $PGDATABASE@$PGHOST:$PGPORT"
read -r -p "This will overwrite data in '$PGDATABASE'. Continue? [y/N] " ans
[ "$ans" = "y" ] || { echo "aborted"; exit 1; }

# Optional clean slate (uncomment to drop+recreate before load):
# psql --host="$PGHOST" --port="$PGPORT" --username="$PGUSER" -d postgres \
#   -c "DROP DATABASE IF EXISTS $PGDATABASE" -c "CREATE DATABASE $PGDATABASE"

gunzip -c "$DUMP" | psql --host="$PGHOST" --port="$PGPORT" --username="$PGUSER" -d "$PGDATABASE"
echo "[restore] db done. Verify: SELECT count(*) FROM organizations;"

if [ -n "$UPLOADS_ARCHIVE" ]; then
  : "${UPLOADS_TARGET_DIR:?set UPLOADS_TARGET_DIR to the path api/worker read as UPLOAD_DIR to restore uploads}"
  echo "[restore] restoring uploads $UPLOADS_ARCHIVE -> $UPLOADS_TARGET_DIR"
  read -r -p "This will overwrite files in '$UPLOADS_TARGET_DIR'. Continue? [y/N] " ans2
  if [ "$ans2" = "y" ]; then
    mkdir -p "$UPLOADS_TARGET_DIR"
    tar -xzf "$UPLOADS_ARCHIVE" -C "$UPLOADS_TARGET_DIR"
    echo "[restore] uploads done."
  else
    echo "[restore] uploads restore skipped — documents rows will point at missing files until this is run."
  fi
else
  echo "[restore] NOTE: no uploads archive given — every restored 'documents' row's storage_path/"
  echo "          docling_json_path points at a file that was NOT restored. Pass the matching"
  echo "          vicero_uploads_<timestamp>.tar.gz from the same backup run if one exists."
fi
