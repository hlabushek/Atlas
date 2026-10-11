#!/usr/bin/env bash
# Только подготовка: проверяем архив, сохраняем текущие образы и испытываем восстановленную БД.
set -Eeuo pipefail
umask 077
APP=/opt/atlas/app
RELEASE=/opt/atlas/staging/voice-center-20261002
ARCHIVE=/opt/atlas/staging/atlas-voice-center-20261002.tar.gz
[[ "${1:-}" =~ ^[a-fA-F0-9]{64}$ ]] || exit 1
[[ "$(sha256sum "$ARCHIVE" | cut -d' ' -f1)" == "${1,,}" ]] || exit 1
[[ ! -e "$RELEASE" ]] || { echo 'Release already exists'; exit 1; }
[[ "$(docker exec atlas-postgres-1 psql -U atlas -d atlas -At -c 'SELECT version_num FROM alembic_version')" == '0014' ]] || exit 1
install -d -m 0700 "$RELEASE"
tar -xzf "$ARCHIVE" -C "$RELEASE"
[[ "$(sha256sum "$APP/backend/requirements.lock" | cut -d' ' -f1)" == "$(sha256sum "$RELEASE/backend/requirements.lock" | cut -d' ' -f1)" ]] || exit 1
tar --exclude=__pycache__ --exclude='*.pyc' -czf "$RELEASE/pre-center-source.tar.gz" -C "$APP" backend/atlas backend/migrations backend/alembic.ini backend/pyproject.toml backend/requirements.lock frontend/src frontend/package.json frontend/package-lock.json frontend/nginx.conf ops/desktop-gateway.compose.yaml
for service in api worker scheduler telegram migrate web; do
    docker tag "atlas-$service:latest" "atlas-$service:pre-center-20261002"
done
docker build --pull=false --build-arg BASE_IMAGE=atlas-api:pre-center-20261002 -f "$RELEASE/ops/voice-rollout/backend.Dockerfile" -t atlas-api:center-20261002 "$RELEASE"
docker build --pull=false --build-arg BASE_IMAGE=atlas-web:pre-center-20261002 -f "$RELEASE/ops/voice-rollout/frontend.Dockerfile" -t atlas-web:center-20261002 "$RELEASE"
docker run --rm --network none --entrypoint python atlas-api:center-20261002 -m pip check
bash -n "$RELEASE/ops/voice-rollout/center-activate.sh"
bash -n "$RELEASE/ops/backup/atlas-restore-drill.sh"
/usr/local/sbin/atlas-backup
backup=$(find /opt/atlas/backups/encrypted -maxdepth 1 -type f -name 'atlas-*.dump.gpg' -printf '%T@ %p\n' | sort -nr | head -n 1 | cut -d' ' -f2-)
[[ -f "$backup" ]] || exit 1
suffix=$(od -An -N8 -tx1 /dev/urandom | tr -d ' \n')
database="atlas_voice_drill_$suffix"
[[ "$database" =~ ^atlas_voice_drill_[a-f0-9]{16}$ ]] || exit 1
created=0
cleanup() {
    if (( created )); then docker exec atlas-postgres-1 dropdb -U atlas --if-exists "$database" >/dev/null; fi
}
trap cleanup EXIT
docker exec atlas-postgres-1 createdb -U atlas "$database"
created=1
gpg --batch --pinentry-mode loopback --passphrase-file /etc/atlas-backup/passphrase --decrypt "$backup" | docker exec -i atlas-postgres-1 pg_restore -U atlas -d "$database" --exit-on-error --no-owner
docker run --rm --network atlas_default --env-file "$APP/.env" --env PYTHONPATH=/app --mount "type=bind,src=$RELEASE/ops/voice-rollout/migrate_restored.py,dst=/run/migrate_restored.py,readonly" --entrypoint python atlas-api:center-20261002 /run/migrate_restored.py "$database" 0015
touch "$RELEASE/migration-drill-passed"
echo 'PASS: prepared, production unchanged; isolated migration verified'
