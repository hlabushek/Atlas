#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

APP_DIR=/opt/atlas/app
BACKUP_DIR=/opt/atlas/backups/encrypted
KEY_FILE=/etc/atlas-backup/passphrase
PROJECT=(docker compose --project-name atlas --file "$APP_DIR/compose.yaml" --file "$APP_DIR/compose.production.yaml")

install -d -o root -g root -m 0700 "$BACKUP_DIR"
[[ -r "$KEY_FILE" ]] || { echo "Atlas backup key is unavailable" >&2; exit 1; }

stamp=$(date -u +%Y%m%dT%H%M%SZ)
target="$BACKUP_DIR/atlas-$stamp.dump.gpg"
temporary=$(mktemp "$BACKUP_DIR/.atlas-$stamp.XXXXXX.gpg")
trap 'rm -f -- "$temporary"' EXIT

"${PROJECT[@]}" exec -T postgres pg_dump -U atlas -d atlas -Fc \
  | gpg --batch --yes --pinentry-mode loopback --passphrase-file "$KEY_FILE" \
      --symmetric --cipher-algo AES256 --s2k-mode 3 --s2k-digest-algo SHA512 \
      --output "$temporary"
chmod 0600 "$temporary"
mv -- "$temporary" "$target"
trap - EXIT

# Читаем архив целиком без сохранения plaintext: --list может завершиться после
# TOC раньше gpg и вызвать ложный Broken pipe при set -o pipefail.
gpg --batch --pinentry-mode loopback --passphrase-file "$KEY_FILE" --decrypt "$target" \
  | "${PROJECT[@]}" exec -T postgres pg_restore --file=/dev/null

find "$BACKUP_DIR" -maxdepth 1 -type f -name 'atlas-*.dump.gpg' -mtime +30 -delete
echo "Encrypted Atlas backup verified: $(basename "$target") ($(stat -c %s "$target") bytes)"
