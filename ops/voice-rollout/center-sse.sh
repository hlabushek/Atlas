#!/usr/bin/env bash
# Проверенный внешний vhost + внутренний web image; остальные сервисы не меняются.
set -Eeuo pipefail
APP=/opt/atlas/app
RELEASE=/opt/atlas/staging/voice-center-finish-20261003
TARGET=/etc/nginx/sites-available/atlas.prism-news.xyz
BACKUP=/opt/atlas/staging/pre-voice-sse-20261003.conf
[[ "$(readlink -f /etc/nginx/sites-enabled/atlas.prism-news.xyz)" == "$TARGET" ]] || exit 1
[[ "$(sha256sum "$TARGET" | cut -d' ' -f1)" == '6ee9b56c5f4f75ccb7b7af70e19297db24b77ed4c5b501f22c63fc2882896447' ]] || exit 1
[[ ! -e "$BACKUP" ]] || exit 1
cp -p "$TARGET" "$BACKUP"
install -m 0644 /opt/atlas/staging/atlas-voice-sse-20261003.conf "$TARGET"
if ! nginx -t; then
    cp -p "$BACKUP" "$TARGET"
    exit 1
fi
systemctl reload nginx
cp /opt/atlas/staging/atlas-web-sse-20261003.conf "$APP/frontend/nginx.conf"
cp /opt/atlas/staging/atlas-web-sse-20261003.conf "$RELEASE/frontend/nginx.conf"
docker tag atlas-web:latest atlas-web:pre-center-sse-20261003
docker build --pull=false --build-arg BASE_IMAGE=atlas-web:pre-center-sse-20261003 -f "$RELEASE/ops/voice-rollout/frontend.Dockerfile" -t atlas-web:center-sse-20261003 "$RELEASE"
docker run --rm --network none atlas-web:center-sse-20261003 nginx -t
docker tag atlas-web:center-sse-20261003 atlas-web:latest
docker compose --project-name atlas --file "$APP/compose.yaml" --file "$APP/compose.production.yaml" --file "$APP/ops/desktop-gateway.compose.yaml" up -d --no-build --no-deps --force-recreate web
install -m 0644 /opt/atlas/staging/center-sse-20261003.sh "$APP/ops/voice-rollout/center-sse.sh"
echo 'PASS: оба прокси проверены; owner SSE без буферизации; старый vhost/image сохранены'
