#!/usr/bin/env bash
# После успешного drill; меняет только Atlas, без downgrade/drop production.
set -Eeuo pipefail
umask 077
APP=/opt/atlas/app
RELEASE=/opt/atlas/staging/voice-20261002
PROJECT=(docker compose --project-name atlas --file "$APP/compose.yaml" --file "$APP/compose.production.yaml" --file "$APP/ops/desktop-gateway.compose.yaml")
[[ -f "$RELEASE/migration-drill-passed" && -f "$RELEASE/pre-voice-source.tar.gz" ]] || exit 1
docker image inspect atlas-api:voice-20261002 >/dev/null
docker image inspect atlas-web:voice-20261002 >/dev/null
revision=$(docker exec atlas-postgres-1 psql -U atlas -d atlas -At -c 'SELECT version_num FROM alembic_version')
[[ "$revision" == '0011' ]] || { echo 'Unexpected production revision'; exit 1; }
"${PROJECT[@]}" config --quiet
monitor_active=0
if systemctl is-active --quiet atlas-monitor.timer; then
    monitor_active=1
    systemctl stop atlas-monitor.timer
fi
finished=0
cleanup() {
    if (( monitor_active )); then systemctl start atlas-monitor.timer; fi
    if (( ! finished )); then echo 'FAIL: rollout interrupted; preserved old images/source available; do not downgrade production DB'; fi
}
trap cleanup EXIT
"${PROJECT[@]}" stop --timeout 20 api worker scheduler telegram desktop-gateway
counts_query='SELECT (SELECT count(*) FROM conversations),(SELECT count(*) FROM messages),(SELECT count(*) FROM memories),(SELECT count(*) FROM tasks),(SELECT count(*) FROM devices),(SELECT count(*) FROM model_usage)'
before=$(docker exec atlas-postgres-1 psql -U atlas -d atlas -At -c "$counts_query")
cp -a "$RELEASE/backend/atlas/." "$APP/backend/atlas/"
cp -a "$RELEASE/backend/migrations/." "$APP/backend/migrations/"
cp "$RELEASE/backend/alembic.ini" "$RELEASE/backend/pyproject.toml" "$RELEASE/backend/requirements.lock" "$APP/backend/"
cp -a "$RELEASE/frontend/src/." "$APP/frontend/src/"
cp -a "$RELEASE/frontend/public/." "$APP/frontend/public/"
cp "$RELEASE/frontend/package.json" "$RELEASE/frontend/package-lock.json" "$RELEASE/frontend/tsconfig.json" "$RELEASE/frontend/vite.config.ts" "$RELEASE/frontend/index.html" "$RELEASE/frontend/nginx.conf" "$APP/frontend/"
install -d -m 0755 "$APP/ops/voice-rollout"
cp "$RELEASE/ops/voice-rollout/"* "$APP/ops/voice-rollout/"
cp "$RELEASE/ops/desktop-gateway.compose.yaml" "$APP/ops/desktop-gateway.compose.yaml"
cp -p /usr/local/sbin/atlas-restore-drill "$RELEASE/pre-voice-restore-drill.sh"
install -o root -g root -m 0750 "$RELEASE/ops/backup/atlas-restore-drill.sh" /usr/local/sbin/atlas-restore-drill
cp "$RELEASE/ops/backup/atlas-restore-drill.sh" "$APP/ops/backup/atlas-restore-drill.sh"
cp -p /usr/local/sbin/atlas-backup "$RELEASE/pre-voice-backup.sh"
install -o root -g root -m 0750 "$RELEASE/ops/backup/atlas-backup.sh" /usr/local/sbin/atlas-backup
cp "$RELEASE/ops/backup/atlas-backup.sh" "$APP/ops/backup/atlas-backup.sh"
for service in api worker scheduler telegram migrate; do
    docker tag atlas-api:voice-20261002 "atlas-$service:latest"
done
docker tag atlas-web:voice-20261002 atlas-web:latest
"${PROJECT[@]}" run --rm --no-deps migrate
after=$(docker exec atlas-postgres-1 psql -U atlas -d atlas -At -c "$counts_query")
[[ "$after" == "$before" ]] || { echo 'Production row counts changed during migration'; exit 1; }
revision=$(docker exec atlas-postgres-1 psql -U atlas -d atlas -At -c 'SELECT version_num FROM alembic_version')
[[ "$revision" == '0014' ]] || exit 1
echo 'PASS: production Alembic 0014; original row counts preserved'
"${PROJECT[@]}" up -d --no-build --no-deps --force-recreate api worker scheduler telegram web desktop-gateway
for attempt in {1..20}; do
    if curl --fail --silent --max-time 5 https://atlas.prism-news.xyz/api/health >/dev/null; then
        finished=1
        break
    fi
    sleep 1
done
(( finished )) || exit 1
"${PROJECT[@]}" ps --format 'table {{.Name}}\t{{.Status}}'
echo 'PASS: Atlas code/gateway/interface updated; HTTPS health OK'
