#!/usr/bin/env bash
# Подготовка без остановки production. Запускается только на текущем VPS Atlas.
set -Eeuo pipefail
umask 077
APP=/opt/atlas/app
RELEASE=/opt/atlas/staging/voice-20261002
ARCHIVE=/opt/atlas/staging/atlas-voice-release-20261002.tar.gz
[[ "${1:-}" =~ ^[a-fA-F0-9]{64}$ ]] || exit 1
[[ "$(sha256sum "$ARCHIVE" | cut -d' ' -f1)" == "${1,,}" ]] || exit 1
[[ ! -e "$RELEASE" ]] || { echo 'Release directory already exists'; exit 1; }
install -d -m 0700 "$RELEASE"
tar -xzf "$ARCHIVE" -C "$RELEASE"
[[ "$(sha256sum "$APP/backend/requirements.lock" | cut -d' ' -f1)" == "$(sha256sum "$RELEASE/backend/requirements.lock" | cut -d' ' -f1)" ]] || { echo 'Dependency lock mismatch'; exit 1; }
tar --exclude=__pycache__ --exclude='*.pyc' -czf "$RELEASE/pre-voice-source.tar.gz" -C "$APP" backend/atlas backend/migrations backend/alembic.ini backend/pyproject.toml backend/requirements.lock frontend/src frontend/package.json frontend/package-lock.json frontend/nginx.conf ops/desktop-gateway.compose.yaml
for service in api worker scheduler telegram migrate web; do
    docker tag "atlas-$service:latest" "atlas-$service:pre-voice-20261002"
done
docker build --pull=false --build-arg BASE_IMAGE=atlas-api:pre-voice-20261002 -f "$RELEASE/ops/voice-rollout/backend.Dockerfile" -t atlas-api:voice-20261002 "$RELEASE"
docker build --pull=false --build-arg BASE_IMAGE=atlas-web:pre-voice-20261002 -f "$RELEASE/ops/voice-rollout/frontend.Dockerfile" -t atlas-web:voice-20261002 "$RELEASE"
docker run --rm --network none --entrypoint python atlas-api:voice-20261002 -m pip check
bash -n "$RELEASE/ops/backup/atlas-restore-drill.sh"
bash "$RELEASE/ops/voice-rollout/drill.sh"
echo 'PASS: release prepared; production unchanged'
