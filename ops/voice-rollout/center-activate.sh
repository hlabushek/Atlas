#!/usr/bin/env bash
# Обновление только Atlas после drill; PostgreSQL/Redis и чужие проекты не останавливаются.
set -Eeuo pipefail
umask 077
APP=/opt/atlas/app
RELEASE=/opt/atlas/staging/voice-center-20261002
PROJECT=(docker compose --project-name atlas --file "$APP/compose.yaml" --file "$APP/compose.production.yaml" --file "$APP/ops/desktop-gateway.compose.yaml")
[[ -f "$RELEASE/migration-drill-passed" && -f "$RELEASE/pre-center-source.tar.gz" ]] || exit 1
[[ "$(docker exec atlas-postgres-1 psql -U atlas -d atlas -At -c 'SELECT version_num FROM alembic_version')" == '0014' ]] || exit 1
docker image inspect atlas-api:center-20261002 >/dev/null
docker image inspect atlas-web:center-20261002 >/dev/null
"${PROJECT[@]}" config --quiet
monitor_active=0
if systemctl is-active --quiet atlas-monitor.timer; then
    monitor_active=1
    systemctl stop atlas-monitor.timer
fi
finished=0
cleanup() {
    if (( monitor_active )); then systemctl start atlas-monitor.timer; fi
    if (( ! finished )); then echo 'FAIL: inspect deployment; old images/source preserved, no automatic downgrade'; fi
}
trap cleanup EXIT
"${PROJECT[@]}" stop --timeout 20 api worker scheduler telegram desktop-gateway
counts_query="SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename NOT IN ('alembic_version') ORDER BY tablename"
mapfile -t original_tables < <(docker exec atlas-postgres-1 psql -U atlas -d atlas -At -c "$counts_query")
counts() {
    for table in "${original_tables[@]}"; do
        [[ "$table" =~ ^[a-z_]+$ ]] || exit 1
        docker exec atlas-postgres-1 psql -U atlas -d atlas -At -c "SELECT '$table', count(*) FROM $table"
    done
}
before=$(counts)
cp -a "$RELEASE/backend/atlas/." "$APP/backend/atlas/"
cp -a "$RELEASE/backend/migrations/." "$APP/backend/migrations/"
cp "$RELEASE/backend/alembic.ini" "$RELEASE/backend/pyproject.toml" "$RELEASE/backend/requirements.lock" "$APP/backend/"
cp -a "$RELEASE/frontend/src/." "$APP/frontend/src/"
cp -a "$RELEASE/frontend/public/." "$APP/frontend/public/"
cp "$RELEASE/frontend/package.json" "$RELEASE/frontend/package-lock.json" "$RELEASE/frontend/tsconfig.json" "$RELEASE/frontend/vite.config.ts" "$RELEASE/frontend/index.html" "$RELEASE/frontend/nginx.conf" "$APP/frontend/"
cp -p /usr/local/sbin/atlas-restore-drill "$RELEASE/pre-center-restore-drill.sh"
install -o root -g root -m 0750 "$RELEASE/ops/backup/atlas-restore-drill.sh" /usr/local/sbin/atlas-restore-drill
cp "$RELEASE/ops/backup/atlas-restore-drill.sh" "$APP/ops/backup/atlas-restore-drill.sh"
install -d -m 0755 "$APP/ops/voice-rollout"
cp "$RELEASE/ops/voice-rollout/"* "$APP/ops/voice-rollout/"
for service in api worker scheduler telegram migrate; do
    docker tag atlas-api:center-20261002 "atlas-$service:latest"
done
docker tag atlas-web:center-20261002 atlas-web:latest
"${PROJECT[@]}" run --rm --no-deps migrate
[[ "$(counts)" == "$before" ]] || { echo 'Original row counts changed'; exit 1; }
[[ "$(docker exec atlas-postgres-1 psql -U atlas -d atlas -At -c 'SELECT version_num FROM alembic_version')" == '0015' ]] || exit 1
echo 'PASS: production 0015; all original table row counts preserved'
"${PROJECT[@]}" up -d --no-build --no-deps --force-recreate api worker scheduler telegram web desktop-gateway
for attempt in {1..20}; do
    if curl --fail --silent --max-time 5 https://atlas.prism-news.xyz/api/health >/dev/null; then
        finished=1
        break
    fi
    sleep 1
done
(( finished )) || exit 1
echo 'PASS: rollout healthy; backups and old images retained'
