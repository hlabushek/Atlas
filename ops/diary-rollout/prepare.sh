#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
APP=/opt/atlas/app
RELEASE=/opt/atlas/staging/diary-20261004
ARCHIVE=/opt/atlas/staging/atlas-diary-20261004.tar.gz
[[ "${1:-}" =~ ^[a-fA-F0-9]{64}$ ]]
[[ "$(sha256sum "$ARCHIVE" | cut -d' ' -f1)" == "${1,,}" ]]
[[ ! -e "$RELEASE" ]]
[[ "$(docker exec atlas-postgres-1 psql -U atlas -d atlas -At -c 'SELECT version_num FROM alembic_version')" == '0023' ]]
install -d -m 0700 "$RELEASE"
tar -xzf "$ARCHIVE" -C "$RELEASE"
tar --exclude=__pycache__ --exclude='*.pyc' -czf "$RELEASE/pre-source.tar.gz" -C "$APP" backend/atlas backend/migrations frontend/src frontend/dist
docker tag atlas-api:latest atlas-api:pre-diary-20261004
docker tag atlas-web:latest atlas-web:pre-diary-20261004
docker build --pull=false -f "$RELEASE/backend/Dockerfile" -t atlas-api:diary-20261004 "$RELEASE/backend"
docker build --pull=false -f "$RELEASE/ops/diary-rollout/Web.Dockerfile" -t atlas-web:diary-20261004 "$RELEASE"
docker run --rm --network none --entrypoint python atlas-api:diary-20261004 -m pip check
docker run --rm --network none atlas-web:diary-20261004 nginx -t
cd "$APP"
/usr/local/sbin/atlas-backup </dev/null
backup=$(find /opt/atlas/backups/encrypted -maxdepth 1 -type f -name 'atlas-*.dump.gpg' -printf '%T@ %p\n' | sort -nr | head -n 1 | cut -d' ' -f2-)
[[ -f "$backup" ]]
suffix=$(od -An -N8 -tx1 /dev/urandom | tr -d ' \n')
database="atlas_diary_drill_$suffix"
[[ "$database" =~ ^atlas_diary_drill_[a-f0-9]{16}$ ]]
created=0
cleanup() { if (( created )); then docker exec atlas-postgres-1 dropdb -U atlas --if-exists "$database" >/dev/null; fi; }
trap cleanup EXIT
docker exec atlas-postgres-1 createdb -U atlas "$database"
created=1
gpg --batch --pinentry-mode loopback --passphrase-file /etc/atlas-backup/passphrase --decrypt "$backup" | docker exec -i atlas-postgres-1 pg_restore -U atlas -d "$database" --exit-on-error --no-owner
docker run --rm --network atlas_default --env-file "$APP/.env" --env PYTHONPATH=/app --mount "type=bind,src=$RELEASE/ops/diary-rollout/migrate_restored.py,dst=/run/migrate_restored.py,readonly" --entrypoint python atlas-api:diary-20261004 /run/migrate_restored.py "$database"
touch "$RELEASE/drill-passed"
echo 'PASS: diary release prepared; restored-copy migration verified; production unchanged'
