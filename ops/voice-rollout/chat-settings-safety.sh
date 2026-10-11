#!/usr/bin/env bash
# Дополнительная защита реальных ответов на демо-уведомления. Без миграций/очистки.
set -Eeuo pipefail
umask 077
APP=/opt/atlas/app
RELEASE=/opt/atlas/staging/chat-settings-20261003
PROJECT=(docker compose --project-name atlas --file "$APP/compose.yaml" --file "$APP/compose.production.yaml" --file "$APP/ops/desktop-gateway.compose.yaml")
[[ "${1:-}" =~ ^[a-f0-9]{64}$ ]] || exit 1
[[ "$(sha256sum /opt/atlas/staging/demo-reset-safe.py | cut -d' ' -f1)" == "$1" ]] || exit 1
[[ "$(docker exec atlas-postgres-1 psql -U atlas -d atlas -At -c 'SELECT version_num FROM alembic_version')" == '0017' ]] || exit 1
[[ ! -e "$RELEASE/pre-safety-demo-reset.py" ]] || exit 1
cp "$APP/backend/atlas/demo_reset.py" "$RELEASE/pre-safety-demo-reset.py"
cp /opt/atlas/staging/demo-reset-safe.py "$RELEASE/backend/atlas/demo_reset.py"
docker build --pull=false --build-arg BASE_IMAGE=atlas-api:chat-settings-20261003 -f "$RELEASE/ops/voice-rollout/backend.Dockerfile" -t atlas-api:chat-settings-safety-20261003 "$RELEASE"
docker run --rm --network none --entrypoint python atlas-api:chat-settings-safety-20261003 -m pip check
/usr/local/sbin/atlas-backup
monitor_active=0
if systemctl is-active --quiet atlas-monitor.timer; then monitor_active=1; systemctl stop atlas-monitor.timer; fi
cleanup() { if (( monitor_active )); then systemctl start atlas-monitor.timer; fi; }
trap cleanup EXIT
"${PROJECT[@]}" stop --timeout 20 api worker scheduler telegram desktop-gateway
cp /opt/atlas/staging/demo-reset-safe.py "$APP/backend/atlas/demo_reset.py"
for service in api worker scheduler telegram migrate; do docker tag atlas-api:chat-settings-safety-20261003 "atlas-$service:latest"; done
"${PROJECT[@]}" up -d --no-build --no-deps --force-recreate api worker scheduler telegram desktop-gateway
for attempt in {1..20}; do
 if curl --fail --silent --max-time 5 https://atlas.prism-news.xyz/api/health >/dev/null; then echo 'PASS: reply guard deployed; no cleanup repeated or database changes'; exit 0; fi
 sleep 1
done
exit 1
