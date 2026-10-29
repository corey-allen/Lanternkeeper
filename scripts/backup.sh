#!/usr/bin/env sh
# Back up the database and private document storage of a running Compose stack.
# Usage: scripts/backup.sh [backup_dir]
# Backups contain your private uploaded documents. Store them as privately as the originals.
set -eu
DIR="${1:-backups}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
COMPOSE="docker compose -f infra/compose.yaml --env-file .env"
mkdir -p "$DIR"
umask 077

$COMPOSE exec -T db pg_dump -U lanternkeeper -d lanternkeeper -Fc > "$DIR/db-$STAMP.dump"
$COMPOSE exec -T api tar -C /data -czf - storage > "$DIR/storage-$STAMP.tgz"
( cd "$DIR" && sha256sum "db-$STAMP.dump" "storage-$STAMP.tgz" > "SHA256SUMS-$STAMP" )
echo "Backup written: $DIR/db-$STAMP.dump $DIR/storage-$STAMP.tgz"
