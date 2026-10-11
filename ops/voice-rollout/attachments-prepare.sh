#!/usr/bin/env bash
# Подготовка и проверка миграции на отдельной восстановленной БД.
set -Eeuo pipefail
umask 077
APP=/opt/atlas/app
RELEASE=/opt/atlas/staging/attachments-20261003
ARCHIVE=/opt/atlas/staging/atlas-attachments-20261003.tar.gz
[[ "${1:-}" =~ ^[a-fA-F0-9]{64}$ ]] || exit 1
[[ "$(sha256sum "$ARCHIVE" | cut -d' ' -f1)" == "${1,,}" ]] || exit 1
[[ ! -e "$RELEASE" ]] || exit 1
[[ "$(docker exec atlas-postgres-1 psql -U atlas -d atlas -At -c 'SELECT version_num FROM alembic_version')" == '0017' ]] || exit 1
install -d -m 0700 "$RELEASE"
tar -xzf "$ARCHIVE" -C "$RELEASE"
[[ "$(tr -d '\r' < "$APP/backend/requirements.lock" | sha256sum | cut -d' ' -f1)" == "$(tr -d '\r' < "$RELEASE/backend/requirements.lock" | grep -Ev '^(pypdf==6.19.0|Pillow==12.3.0)$' | sha256sum | cut -d' ' -f1)" ]] || exit 1
tar --exclude=__pycache__ --exclude='*.pyc' -czf "$RELEASE/pre-ui-source.tar.gz" -C "$APP" backend/atlas backend/migrations backend/alembic.ini backend/requirements.lock frontend/src frontend/public frontend/package.json frontend/package-lock.json frontend/nginx.conf
for service in api worker scheduler telegram migrate web; do
 docker tag "atlas-$service:latest" "atlas-$service:pre-attachments-20261003"
done
docker build --pull=false --build-arg BASE_IMAGE=atlas-api:pre-attachments-20261003 -f "$RELEASE/ops/voice-rollout/attachments.Dockerfile" -t atlas-api:attachments-20261003 "$RELEASE"
docker build --pull=false --build-arg BASE_IMAGE=atlas-web:pre-attachments-20261003 -f "$RELEASE/ops/voice-rollout/frontend.Dockerfile" -t atlas-web:attachments-20261003 "$RELEASE"
docker run --rm --network none --entrypoint python atlas-api:attachments-20261003 -m pip check
docker run --rm --network none atlas-web:attachments-20261003 nginx -t
bash -n "$RELEASE/ops/voice-rollout/attachments-activate.sh"
bash -n "$RELEASE/ops/backup/atlas-restore-drill.sh"
/usr/local/sbin/atlas-backup </dev/null
backup=$(find /opt/atlas/backups/encrypted -maxdepth 1 -type f -name 'atlas-*.dump.gpg' -printf '%T@ %p\n' | sort -nr | head -n 1 | cut -d' ' -f2-)
[[ -f "$backup" ]] || exit 1
suffix=$(od -An -N8 -tx1 /dev/urandom | tr -d ' \n')
database="atlas_voice_drill_$suffix"
[[ "$database" =~ ^atlas_voice_drill_[a-f0-9]{16}$ ]] || exit 1
created=0
cleanup() { if (( created )); then docker exec atlas-postgres-1 dropdb -U atlas --if-exists "$database" >/dev/null; fi; }
trap cleanup EXIT
docker exec atlas-postgres-1 createdb -U atlas "$database"
created=1
gpg --batch --pinentry-mode loopback --passphrase-file /etc/atlas-backup/passphrase --decrypt "$backup" | docker exec -i atlas-postgres-1 pg_restore -U atlas -d "$database" --exit-on-error --no-owner
docker run --rm --network atlas_default --env-file "$APP/.env" --env PYTHONPATH=/app --mount "type=bind,src=$RELEASE/ops/voice-rollout/migrate_restored.py,dst=/run/migrate_restored.py,readonly" --entrypoint python atlas-api:attachments-20261003 /run/migrate_restored.py "$database" 0018
touch "$RELEASE/migration-drill-passed"
echo 'PASS: attachments prepared; restored-copy migration verified; production unchanged'
