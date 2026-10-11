#!/usr/bin/env bash
# Только Atlas; не меняет модели, credentials, голосовую политику и чужие проекты.
set -Eeuo pipefail
umask 077
APP=/opt/atlas/app
RELEASE=/opt/atlas/staging/ui-workspace-20261003
PROJECT=(docker compose --project-name atlas --file "$APP/compose.yaml" --file "$APP/compose.production.yaml" --file "$APP/ops/desktop-gateway.compose.yaml")
[[ -f "$RELEASE/migration-drill-passed" && -f "$RELEASE/pre-ui-source.tar.gz" ]] || exit 1
[[ "$(docker exec atlas-postgres-1 psql -U atlas -d atlas -At -c 'SELECT version_num FROM alembic_version')" == '0015' ]] || exit 1
"${PROJECT[@]}" config --quiet
/usr/local/sbin/atlas-backup
monitor_active=0
if systemctl is-active --quiet atlas-monitor.timer; then monitor_active=1; systemctl stop atlas-monitor.timer; fi
finished=0
cleanup() {
 if (( monitor_active )); then systemctl start atlas-monitor.timer; fi
 if (( ! finished )); then echo 'FAIL: old images/source retained; no automatic downgrade or restore over live data'; fi
}
trap cleanup EXIT
"${PROJECT[@]}" stop --timeout 20 api worker scheduler telegram desktop-gateway
mapfile -t original_tables < <(docker exec atlas-postgres-1 psql -U atlas -d atlas -At -c "SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename<>'alembic_version' ORDER BY tablename")
counts() {
 for table in "${original_tables[@]}"; do
  [[ "$table" =~ ^[a-z_]+$ ]] || exit 1
  docker exec atlas-postgres-1 psql -U atlas -d atlas -At -c "SELECT '$table', count(*) FROM $table"
 done
}
before=$(counts)
cp -a "$RELEASE/backend/atlas/." "$APP/backend/atlas/"
cp -a "$RELEASE/backend/migrations/." "$APP/backend/migrations/"
cp -a "$RELEASE/frontend/src/." "$APP/frontend/src/"
cp -a "$RELEASE/frontend/public/." "$APP/frontend/public/"
cp "$RELEASE/frontend/nginx.conf" "$APP/frontend/nginx.conf"
cp -p /usr/local/sbin/atlas-restore-drill "$RELEASE/pre-ui-restore-drill.sh"
install -o root -g root -m 0750 "$RELEASE/ops/backup/atlas-restore-drill.sh" /usr/local/sbin/atlas-restore-drill
cp "$RELEASE/ops/backup/atlas-restore-drill.sh" "$APP/ops/backup/atlas-restore-drill.sh"
for service in api worker scheduler telegram migrate; do docker tag atlas-api:ui-workspace-20261003 "atlas-$service:latest"; done
docker tag atlas-web:ui-workspace-20261003 atlas-web:latest
"${PROJECT[@]}" run --rm --no-deps migrate
[[ "$(counts)" == "$before" ]] || { echo 'Original row counts changed'; exit 1; }
[[ "$(docker exec atlas-postgres-1 psql -U atlas -d atlas -At -c 'SELECT version_num FROM alembic_version')" == '0016' ]] || exit 1
"${PROJECT[@]}" up -d --no-build --no-deps --force-recreate api worker scheduler telegram desktop-gateway web
for attempt in {1..20}; do
 if curl --fail --silent --max-time 5 https://atlas.prism-news.xyz/api/health >/dev/null; then finished=1; break; fi
 sleep 1
done
(( finished )) || exit 1
echo 'PASS: UI and spaces deployed; original row counts preserved; model/voice settings unchanged'
