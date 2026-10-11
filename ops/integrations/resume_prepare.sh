#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
RELEASE=/opt/atlas/staging/integrations-20261004
trap 'echo FAIL > "$RELEASE/resume-status"' ERR
[[ -f "$RELEASE/pre-source.tar.gz" ]]
[[ "$(docker exec atlas-postgres-1 psql -U atlas -d atlas -At -c 'SELECT version_num FROM alembic_version')" == '0018' ]]
docker build --pull=false -f "$RELEASE/backend/Dockerfile" -t atlas-api:integrations-20261004 "$RELEASE/backend"
docker build --pull=false -f "$RELEASE/backend/Browser.Dockerfile" -t atlas-browser:integrations-20261004 "$RELEASE/backend"
docker build --pull=false -f "$RELEASE/ops/integrations/Web.Dockerfile" -t atlas-web:integrations-20261004 "$RELEASE"
docker run --rm --network none --entrypoint python atlas-api:integrations-20261004 -m pip check
docker run --rm --network none --entrypoint python atlas-browser:integrations-20261004 -m pip check
docker run --rm --network none atlas-web:integrations-20261004 nginx -t
bash -n "$RELEASE/ops/integrations/drill.sh"
bash -n "$RELEASE/ops/integrations/activate.sh"
bash "$RELEASE/ops/integrations/drill.sh"
echo PASS > "$RELEASE/resume-status"
