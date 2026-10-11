#!/usr/bin/env bash
# Только код и две выбранные роли. Схема/ключи/Whisper/Piper не меняются.
set -Eeuo pipefail
umask 077
APP=/opt/atlas/app
RELEASE=/opt/atlas/staging/dual-models-20261003
ARCHIVE=/opt/atlas/staging/atlas-dual-models-20261003.tar.gz
PROJECT=(docker compose --project-name atlas --file "$APP/compose.yaml" --file "$APP/compose.production.yaml" --file "$APP/ops/desktop-gateway.compose.yaml")
[[ "${1:-}" =~ ^[a-fA-F0-9]{64}$ ]] || exit 1
[[ "$(sha256sum "$ARCHIVE" | cut -d' ' -f1)" == "${1,,}" ]] || exit 1
[[ ! -e "$RELEASE" ]] || exit 1
[[ "$(docker exec atlas-postgres-1 psql -U atlas -d atlas -At -c 'SELECT version_num FROM alembic_version')" == '0015' ]] || exit 1
install -d -m 0700 "$RELEASE"
tar -xzf "$ARCHIVE" -C "$RELEASE"
[[ "$(sha256sum "$APP/backend/requirements.lock" | cut -d' ' -f1)" == "$(sha256sum "$RELEASE/backend/requirements.lock" | cut -d' ' -f1)" ]] || exit 1
tar --exclude=__pycache__ --exclude='*.pyc' -czf "$RELEASE/pre-dual-source.tar.gz" -C "$APP" backend/atlas frontend/src frontend/nginx.conf
for service in api worker scheduler telegram migrate web; do
 docker tag "atlas-$service:latest" "atlas-$service:pre-dual-models-20261003"
done
docker build --pull=false --build-arg BASE_IMAGE=atlas-api:pre-dual-models-20261003 -f "$RELEASE/ops/voice-rollout/backend.Dockerfile" -t atlas-api:dual-models-20261003 "$RELEASE"
docker build --pull=false --build-arg BASE_IMAGE=atlas-web:pre-dual-models-20261003 -f "$RELEASE/ops/voice-rollout/frontend.Dockerfile" -t atlas-web:dual-models-20261003 "$RELEASE"
docker run --rm --network none --entrypoint python atlas-api:dual-models-20261003 -m pip check
docker run --rm --network none atlas-web:dual-models-20261003 nginx -t
"${PROJECT[@]}" config --quiet
/usr/local/sbin/atlas-backup
monitor_active=0
if systemctl is-active --quiet atlas-monitor.timer; then
 monitor_active=1
 systemctl stop atlas-monitor.timer
fi
cleanup() { if (( monitor_active )); then systemctl start atlas-monitor.timer; fi; }
trap cleanup EXIT
"${PROJECT[@]}" stop --timeout 20 api worker scheduler telegram desktop-gateway
cp -a "$RELEASE/backend/atlas/." "$APP/backend/atlas/"
cp -a "$RELEASE/frontend/src/." "$APP/frontend/src/"
for service in api worker scheduler telegram migrate; do
 docker tag atlas-api:dual-models-20261003 "atlas-$service:latest"
done
docker tag atlas-web:dual-models-20261003 atlas-web:latest
"${PROJECT[@]}" up -d --no-build --no-deps --force-recreate api worker scheduler telegram desktop-gateway web
healthy=0
for attempt in {1..20}; do
 if curl --fail --silent --max-time 5 https://atlas.prism-news.xyz/api/health >/dev/null; then healthy=1; break; fi
 sleep 1
done
(( healthy )) || { echo 'FAIL: previous images and source preserved'; exit 1; }
docker exec -i atlas-api-1 python < "$RELEASE/ops/voice-rollout/set-dual-models.py"
docker cp atlas-api-1:/tmp/pre-dual-model-preferences.json "$RELEASE/pre-dual-model-preferences.json"
chmod 0600 "$RELEASE/pre-dual-model-preferences.json"
echo 'PASS: dual models installed; schema 0015, credentials, embeddings and voice hardware settings unchanged'
