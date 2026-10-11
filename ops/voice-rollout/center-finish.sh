#!/usr/bin/env bash
# Малое code-only дополнение после проверенной 0015; БД не изменяется.
set -Eeuo pipefail
umask 077
APP=/opt/atlas/app
RELEASE=/opt/atlas/staging/voice-center-finish-20261003
ARCHIVE=/opt/atlas/staging/atlas-voice-center-finish-20261003.tar.gz
PROJECT=(docker compose --project-name atlas --file "$APP/compose.yaml" --file "$APP/compose.production.yaml" --file "$APP/ops/desktop-gateway.compose.yaml")
[[ "${1:-}" =~ ^[a-fA-F0-9]{64}$ ]] || exit 1
[[ "$(sha256sum "$ARCHIVE" | cut -d' ' -f1)" == "${1,,}" ]] || exit 1
if [[ -e "$RELEASE" ]]; then
    [[ "${2:-}" == 'resume-build' && -f "$RELEASE/pre-finish-source.tar.gz" ]] || exit 1
    [[ "$(docker image inspect atlas-api:latest --format '{{.Id}}')" == "$(docker image inspect atlas-api:pre-center-finish-20261003 --format '{{.Id}}')" ]] || exit 1
fi
[[ "$(docker exec atlas-postgres-1 psql -U atlas -d atlas -At -c 'SELECT version_num FROM alembic_version')" == '0015' ]] || exit 1
install -d -m 0700 "$RELEASE"
tar -xzf "$ARCHIVE" -C "$RELEASE"
[[ "$(sha256sum "$APP/backend/requirements.lock" | cut -d' ' -f1)" == "$(sha256sum "$RELEASE/backend/requirements.lock" | cut -d' ' -f1)" ]] || exit 1
if [[ ! -f "$RELEASE/pre-finish-source.tar.gz" ]]; then
    tar --exclude=__pycache__ --exclude='*.pyc' -czf "$RELEASE/pre-finish-source.tar.gz" -C "$APP" backend/atlas frontend/src frontend/package.json frontend/package-lock.json frontend/nginx.conf
    for service in api worker scheduler telegram migrate web; do
        docker tag "atlas-$service:latest" "atlas-$service:pre-center-finish-20261003"
    done
fi
docker build --pull=false --build-arg BASE_IMAGE=atlas-api:pre-center-finish-20261003 -f "$RELEASE/ops/voice-rollout/backend.Dockerfile" -t atlas-api:center-finish-20261003 "$RELEASE"
docker build --pull=false --build-arg BASE_IMAGE=atlas-web:pre-center-finish-20261003 -f "$RELEASE/ops/voice-rollout/frontend.Dockerfile" -t atlas-web:center-finish-20261003 "$RELEASE"
docker run --rm --network none --entrypoint python atlas-api:center-finish-20261003 -m pip check
"${PROJECT[@]}" config --quiet
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
    docker tag atlas-api:center-finish-20261003 "atlas-$service:latest"
done
docker tag atlas-web:center-finish-20261003 atlas-web:latest
install -d -m 0755 "$APP/ops/voice-rollout"
cp "$RELEASE/ops/voice-rollout/"* "$APP/ops/voice-rollout/"
"${PROJECT[@]}" up -d --no-build --no-deps --force-recreate api worker scheduler telegram web desktop-gateway
for attempt in {1..20}; do
    if curl --fail --silent --max-time 5 https://atlas.prism-news.xyz/api/health >/dev/null; then
        echo 'PASS: code-only finish healthy; database untouched'
        exit 0
    fi
    sleep 1
done
exit 1
