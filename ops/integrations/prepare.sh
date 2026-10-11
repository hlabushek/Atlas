#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
APP=/opt/atlas/app
RELEASE=/opt/atlas/staging/integrations-20261004
ARCHIVE=/opt/atlas/staging/atlas-integrations-20261004.tar.gz
[[ "${1:-}" =~ ^[a-fA-F0-9]{64}$ ]]
[[ "$(sha256sum "$ARCHIVE" | cut -d' ' -f1)" == "${1,,}" ]]
[[ ! -e "$RELEASE" ]]
[[ "$(docker exec atlas-postgres-1 psql -U atlas -d atlas -At -c 'SELECT version_num FROM alembic_version')" == '0018' ]]
install -d -m 0700 "$RELEASE"
tar -xzf "$ARCHIVE" -C "$RELEASE"
tar --exclude=__pycache__ --exclude='*.pyc' -czf "$RELEASE/pre-source.tar.gz" -C "$APP" backend/atlas backend/migrations backend/alembic.ini backend/requirements.lock frontend/src frontend/public frontend/dist frontend/package.json frontend/package-lock.json frontend/nginx.conf
for service in api worker scheduler telegram migrate web; do
 docker tag "atlas-$service:latest" "atlas-$service:pre-integrations-20261004"
done
docker build --pull=false -f "$RELEASE/backend/Dockerfile" -t atlas-api:integrations-20261004 "$RELEASE/backend"
docker build --pull=false -f "$RELEASE/backend/Browser.Dockerfile" -t atlas-browser:integrations-20261004 "$RELEASE/backend"
docker build --pull=false -f "$RELEASE/ops/integrations/Web.Dockerfile" -t atlas-web:integrations-20261004 "$RELEASE"
docker run --rm --network none --entrypoint python atlas-api:integrations-20261004 -m pip check
docker run --rm --network none --entrypoint python atlas-browser:integrations-20261004 -m pip check
docker run --rm --network none atlas-web:integrations-20261004 nginx -t
bash -n "$RELEASE/ops/integrations/drill.sh"
bash -n "$RELEASE/ops/integrations/activate.sh"
bash "$RELEASE/ops/integrations/drill.sh"
echo 'PASS: release prepared; production unchanged'
