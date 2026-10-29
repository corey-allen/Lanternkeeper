#!/usr/bin/env sh
# Restore a backup made by scripts/backup.sh. DESTROYS current database contents and storage.
# Usage: scripts/restore.sh backups/db-<stamp>.dump backups/storage-<stamp>.tgz
set -eu
DB_DUMP="$1"
STORAGE_TGZ="$2"
COMPOSE="docker compose -f infra/compose.yaml --env-file .env"

printf 'This replaces ALL current Lanternkeeper data. Type "restore" to continue: '
read -r answer
[ "$answer" = "restore" ] || { echo "aborted"; exit 1; }

$COMPOSE stop api worker web
$COMPOSE exec -T db pg_restore -U lanternkeeper -d lanternkeeper --clean --if-exists --no-owner < "$DB_DUMP"
$COMPOSE run --rm --no-deps -T --entrypoint sh api -c 'rm -rf /data/storage/* && tar -C /data -xzf -' < "$STORAGE_TGZ"
$COMPOSE run --rm migrate
$COMPOSE start api worker web
echo "Restore complete. Verify with: curl -s http://127.0.0.1:3000/api/v1/health"
