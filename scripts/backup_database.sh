#!/usr/bin/env sh
# Create a PostgreSQL custom dump from the Compose database and retain a
# bounded history. Run from the repository root or set PROJECT_DIR explicitly.
set -eu

PROJECT_DIR=${PROJECT_DIR:-$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)}
BACKUP_DIR=${BACKUP_DIR:-"$PROJECT_DIR/backups"}
KEEP_DAYS=${BACKUP_KEEP_DAYS:-14}

cd "$PROJECT_DIR"
mkdir -p "$BACKUP_DIR"
umask 077

timestamp=$(date -u +%Y%m%d_%H%M%S)
target="$BACKUP_DIR/auction_bot_${timestamp}.dump"

docker compose exec -T postgres sh -c \
  'pg_dump --format=custom --no-owner --no-privileges -U "$POSTGRES_USER" "$POSTGRES_DB"' \
  > "$target"

test -s "$target"
docker run --rm -v "$BACKUP_DIR:/backups:ro" postgres:16-alpine \
  pg_restore --list "/backups/$(basename "$target")" >/dev/null

find "$BACKUP_DIR" -type f -name 'auction_bot_*.dump' -mtime "+$KEEP_DAYS" -delete
printf 'Backup verified: %s\n' "$target"
