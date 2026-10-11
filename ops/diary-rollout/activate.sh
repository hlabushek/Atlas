#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
APP=/opt/atlas/app
RELEASE=/opt/atlas/staging/diary-20261004
[[ -f "$RELEASE/drill-passed" && -f "$RELEASE/pre-source.tar.gz" ]]
[[ "$(docker exec atlas-postgres-1 psql -U atlas -d atlas -At -c 'SELECT version_num FROM alembic_version')" == '0023' ]]
cd "$APP"
/usr/local/sbin/atlas-backup </dev/null
PROJECT=(docker compose --project-name atlas --file "$APP/compose.yaml" --file "$APP/compose.production.yaml" --file "$APP/ops/desktop-gateway.compose.yaml" --file "$APP/compose.browser.yaml")
"${PROJECT[@]}" config --quiet
monitor_active=0
if systemctl is-active --quiet atlas-monitor.timer; then monitor_active=1;systemctl stop atlas-monitor.timer;fi
finished=0
cleanup() {
 if (( monitor_active )); then systemctl start atlas-monitor.timer;fi
 if (( ! finished )); then echo FAIL > "$RELEASE/activate-status";echo 'FAIL: investigate Atlas; no automatic data restore or downgrade';fi
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
cp -a "$RELEASE/frontend/dist/." "$APP/frontend/dist/"
cp -a "$RELEASE/frontend/src/." "$APP/frontend/src/"
for service in api worker scheduler telegram migrate; do docker tag atlas-api:diary-20261004 "atlas-$service:latest";done
docker tag atlas-web:diary-20261004 atlas-web:latest
"${PROJECT[@]}" run --rm --no-deps migrate
[[ "$(counts)" == "$before" ]] || { echo 'Old table row counts changed';exit 1; }
[[ "$(docker exec atlas-postgres-1 psql -U atlas -d atlas -At -c 'SELECT version_num FROM alembic_version')" == '0024' ]]
"${PROJECT[@]}" up -d --no-build --no-deps --force-recreate api worker scheduler telegram desktop-gateway web
for attempt in {1..30}; do
 if curl --fail --silent --max-time 5 https://atlas.prism-news.xyz/api/health >/dev/null; then finished=1;break;fi
 sleep 1
done
(( finished )) || exit 1
echo PASS > "$RELEASE/activate-status"
echo 'PASS: Atlas diary released at 0024; existing row counts preserved'
