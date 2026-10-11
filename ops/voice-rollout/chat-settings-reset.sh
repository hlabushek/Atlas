#!/usr/bin/env bash
# Только одобренное подтверждённое демо; ключи/модели/память/реальные эффекты сохраняются.
set -Eeuo pipefail
umask 077
APP=/opt/atlas/app
RELEASE=/opt/atlas/staging/chat-settings-20261003
PROJECT=(docker compose --project-name atlas --file "$APP/compose.yaml" --file "$APP/compose.production.yaml" --file "$APP/ops/desktop-gateway.compose.yaml")
[[ -f "$RELEASE/demo-reset-drill-passed" ]] || exit 1
[[ "$(docker exec atlas-postgres-1 psql -U atlas -d atlas -At -c 'SELECT version_num FROM alembic_version')" == '0017' ]] || exit 1
/usr/local/sbin/atlas-backup
/usr/local/sbin/atlas-restore-drill
monitor_active=0
if systemctl is-active --quiet atlas-monitor.timer; then monitor_active=1; systemctl stop atlas-monitor.timer; fi
cleanup() {
 "${PROJECT[@]}" up -d --no-build --no-deps api worker scheduler telegram desktop-gateway
 if (( monitor_active )); then systemctl start atlas-monitor.timer; fi
}
trap cleanup EXIT
"${PROJECT[@]}" stop --timeout 20 api worker scheduler telegram desktop-gateway
docker run --rm --network atlas_default --env-file "$APP/.env" --env PYTHONPATH=/app --mount "type=bind,src=$RELEASE/ops/voice-rollout/reset-demo.py,dst=/run/reset-demo.py,readonly" --entrypoint python atlas-api:chat-settings-20261003 /run/reset-demo.py --production-confirmed-demo-only
echo 'PASS: confirmed demo cleared; encrypted pre-reset backup is restorable'
