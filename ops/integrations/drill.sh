#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
APP=/opt/atlas/app
RELEASE=/opt/atlas/staging/integrations-20261004
[[ "$(docker exec atlas-postgres-1 psql -U atlas -d atlas -At -c 'SELECT version_num FROM alembic_version')" == '0018' ]]
/usr/local/sbin/atlas-backup </dev/null
backup=$(find /opt/atlas/backups/encrypted -maxdepth 1 -type f -name 'atlas-*.dump.gpg' -printf '%T@ %p\n' | sort -nr | head -n 1 | cut -d' ' -f2-)
[[ -f "$backup" ]]
suffix=$(od -An -N8 -tx1 /dev/urandom | tr -d ' \n')
database="atlas_integration_drill_$suffix"
[[ "$database" =~ ^atlas_integration_drill_[a-f0-9]{16}$ ]]
created=0
cleanup() { if (( created )); then docker exec atlas-postgres-1 dropdb -U atlas --if-exists "$database" >/dev/null; fi; }
trap cleanup EXIT
docker exec atlas-postgres-1 createdb -U atlas "$database"
created=1
gpg --batch --pinentry-mode loopback --passphrase-file /etc/atlas-backup/passphrase --decrypt "$backup" | docker exec -i atlas-postgres-1 pg_restore -U atlas -d "$database" --exit-on-error --no-owner
docker run --rm --network atlas_default --env-file "$APP/.env" --env PYTHONPATH=/app --mount "type=bind,src=$RELEASE/ops/integrations/migrate_restored.py,dst=/run/migrate_restored.py,readonly" --entrypoint python atlas-api:integrations-20261004 /run/migrate_restored.py "$database"
touch "$RELEASE/drill-passed"
echo 'PASS: integration migration checked on restored copy; production unchanged'
