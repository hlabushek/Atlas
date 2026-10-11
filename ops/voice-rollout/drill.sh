#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
APP=/opt/atlas/app
RELEASE=/opt/atlas/staging/voice-20261002
suffix=$(od -An -N8 -tx1 /dev/urandom | tr -d ' \n')
database="atlas_voice_drill_$suffix"
[[ "$database" =~ ^atlas_voice_drill_[a-f0-9]{16}$ ]] || exit 1
created=0
cleanup() {
    if (( created )); then docker exec atlas-postgres-1 dropdb -U atlas --if-exists "$database" >/dev/null; fi
}
trap cleanup EXIT
backup=$(find /opt/atlas/backups/encrypted -maxdepth 1 -type f -name 'atlas-*.dump.gpg' -printf '%T@ %p\n' | sort -nr | head -n 1 | cut -d' ' -f2-)
[[ -f "$backup" ]] || exit 1
docker exec atlas-postgres-1 createdb -U atlas "$database"
created=1
gpg --batch --pinentry-mode loopback --passphrase-file /etc/atlas-backup/passphrase --decrypt "$backup" | docker exec -i atlas-postgres-1 pg_restore -U atlas -d "$database" --exit-on-error --no-owner
docker run --rm --network atlas_default --env-file "$APP/.env" --env PYTHONPATH=/app --mount "type=bind,src=$RELEASE/ops/voice-rollout/migrate_restored.py,dst=/run/migrate_restored.py,readonly" --entrypoint python atlas-api:voice-20261002 /run/migrate_restored.py "$database"
# Записываем только отметку успеха, никаких данных/секретов.
touch "$RELEASE/migration-drill-passed"
echo 'PASS: migration drill finished; temporary database removed on exit'
