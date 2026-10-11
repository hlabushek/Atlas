#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

APP_DIR=/opt/atlas/app
BACKUP_DIR=/opt/atlas/backups/encrypted
KEY_FILE=/etc/atlas-backup/passphrase
PROJECT=(docker compose --project-name atlas --file "$APP_DIR/compose.yaml" --file "$APP_DIR/compose.production.yaml")

install -d -o root -g root -m 0700 "$BACKUP_DIR"
[[ -r "$KEY_FILE" ]] || { echo "Atlas backup key is unavailable" >&2; exit 1; }
backup=$(find "$BACKUP_DIR" -maxdepth 1 -type f -name 'atlas-*.dump.gpg' -printf '%T@ %p\n' | sort -nr | head -n 1 | cut -d' ' -f2-)
[[ -n "$backup" && -f "$backup" ]] || { echo "No encrypted Atlas backup found" >&2; exit 1; }

suffix=$(od -An -N8 -tx1 /dev/urandom | tr -d ' \n')
database="atlas_restore_drill_$suffix"
[[ "$database" =~ ^atlas_restore_drill_[a-f0-9]{16}$ ]] || exit 1
created=0
cleanup() {
  if (( created )); then "${PROJECT[@]}" exec -T postgres dropdb -U atlas --if-exists "$database" >/dev/null; fi
}
trap cleanup EXIT

"${PROJECT[@]}" exec -T postgres createdb -U atlas "$database"
created=1
gpg --batch --pinentry-mode loopback --passphrase-file "$KEY_FILE" --decrypt "$backup" \
  | "${PROJECT[@]}" exec -T postgres pg_restore -U atlas -d "$database" --exit-on-error --no-owner

revision=$("${PROJECT[@]}" exec -T postgres psql -U atlas -d "$database" -At -v ON_ERROR_STOP=1 -c 'SELECT version_num FROM alembic_version')
tables=$("${PROJECT[@]}" exec -T postgres psql -U atlas -d "$database" -At -v ON_ERROR_STOP=1 -c "SELECT count(*) FROM pg_tables WHERE schemaname='public'")
# Архивы прежних выпусков пригодны; новые миграции поэтапно добавляют таблицы.
[[ "$revision" =~ ^(001[1-9]|002[0-5])$ && "$tables" -ge 30 ]] || { echo "Restore drill verification failed" >&2; exit 1; }
echo "PASS: encrypted backup restored into an isolated temporary database (revision $revision, $tables tables)"
