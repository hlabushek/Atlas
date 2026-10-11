#!/usr/bin/env bash
# Finish schedule-only classification and natural-language list wording; no schema change.
set -Eeuo pipefail
umask 077
APP=/opt/atlas/app
RELEASE=/opt/atlas/staging/diary-20261004
[[ "$(docker exec atlas-postgres-1 psql -U atlas -d atlas -At -c 'SELECT version_num FROM alembic_version')" == '0024' ]]
cd "$APP"
PROJECT=(docker compose --project-name atlas --file "$APP/compose.yaml" --file "$APP/compose.production.yaml" --file "$APP/ops/desktop-gateway.compose.yaml" --file "$APP/compose.browser.yaml")
"${PROJECT[@]}" config --quiet
docker build --pull=false -f "$RELEASE/backend/Dockerfile" -t atlas-api:diary-20261004b "$RELEASE/backend"
docker run --rm --network none --entrypoint python atlas-api:diary-20261004b -m pip check
docker tag atlas-api:latest atlas-api:pre-diary-hotfix-20261004
cp -p "$APP/backend/atlas/skills_api.py" "$RELEASE/pre-hotfix-skills_api.py"
cp -p "$APP/backend/atlas/diary_queries.py" "$RELEASE/pre-hotfix-diary_queries.py"
cp "$RELEASE/backend/atlas/skills_api.py" "$APP/backend/atlas/skills_api.py"
cp "$RELEASE/backend/atlas/diary_queries.py" "$APP/backend/atlas/diary_queries.py"
for service in api worker scheduler telegram migrate;do docker tag atlas-api:diary-20261004b "atlas-$service:latest";done
"${PROJECT[@]}" up -d --no-build --no-deps --force-recreate api worker scheduler telegram
for attempt in {1..30};do
 if curl --fail --silent --max-time 5 https://atlas.prism-news.xyz/api/health >/dev/null;then
  echo 'PASS: diary hotfix active at 0024'
  exit 0
 fi
 sleep 1
done
echo 'FAIL: diary health check; investigate release' >&2
exit 1
