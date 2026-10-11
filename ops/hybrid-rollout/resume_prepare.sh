#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
APP=/opt/atlas/app
RELEASE=/opt/atlas/staging/hybrid-20261011
[[ "$(sha256sum "$APP/backend/requirements.lock" | cut -d' ' -f1)" == "$(sha256sum "$RELEASE/backend/requirements.lock" | cut -d' ' -f1)" ]]
docker build --pull=false --build-arg BASE_IMAGE=atlas-api:pre-hybrid-20261011 -f "$RELEASE/ops/hybrid-rollout/Backend.Dockerfile" -t atlas-api:hybrid-20261011 "$RELEASE"
docker build --pull=false --build-arg BASE_IMAGE=atlas-web:pre-hybrid-20261011 -f "$RELEASE/ops/hybrid-rollout/Web.Dockerfile" -t atlas-web:hybrid-20261011 "$RELEASE"
docker run --rm --network none --entrypoint python atlas-api:hybrid-20261011 -m pip check
docker run --rm --network none atlas-web:hybrid-20261011 nginx -t
cd "$APP"
/usr/local/sbin/atlas-backup </dev/null
backup=$(find /opt/atlas/backups/encrypted -maxdepth 1 -type f -name 'atlas-*.dump.gpg' -printf '%T@ %p\n' | sort -nr | head -n 1 | cut -d' ' -f2-)
[[ -f "$backup" ]]
suffix=$(od -An -N8 -tx1 /dev/urandom | tr -d ' \n')
database="atlas_hybrid_drill_$suffix"
[[ "$database" =~ ^atlas_hybrid_drill_[a-f0-9]{16}$ ]]
created=0
cleanup() { if (( created )); then docker exec atlas-postgres-1 dropdb -U atlas --if-exists "$database" >/dev/null; fi; }
trap cleanup EXIT
docker exec atlas-postgres-1 createdb -U atlas "$database"
created=1
gpg --batch --pinentry-mode loopback --passphrase-file /etc/atlas-backup/passphrase --decrypt "$backup" | docker exec -i atlas-postgres-1 pg_restore -U atlas -d "$database" --exit-on-error --no-owner
docker run --rm --network atlas_default --env-file "$APP/.env" --env PYTHONPATH=/app --mount "type=bind,src=$RELEASE/ops/hybrid-rollout/migrate_restored.py,dst=/run/migrate_restored.py,readonly" --entrypoint python atlas-api:hybrid-20261011 /run/migrate_restored.py "$database"
touch "$RELEASE/drill-passed"
echo 'PASS: hybrid release prepared; restored-copy migration verified; production unchanged'
