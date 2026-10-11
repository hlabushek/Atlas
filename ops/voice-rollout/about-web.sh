#!/usr/bin/env bash
# Публичная информационная страница: только web, без БД и backend.
set -Eeuo pipefail
umask 077
APP=/opt/atlas/app
RELEASE=/opt/atlas/staging/about-20261003
ARCHIVE=/opt/atlas/staging/atlas-about-20261003.tar.gz
PROJECT=(docker compose --project-name atlas --file "$APP/compose.yaml" --file "$APP/compose.production.yaml" --file "$APP/ops/desktop-gateway.compose.yaml")
[[ "${1:-}" =~ ^[a-fA-F0-9]{64}$ ]] || exit 1
[[ "$(sha256sum "$ARCHIVE" | cut -d' ' -f1)" == "${1,,}" ]] || exit 1
[[ ! -e "$RELEASE" ]] || { echo 'Release already exists'; exit 1; }
"${PROJECT[@]}" config --quiet
curl --fail --silent --max-time 10 https://atlas.prism-news.xyz/api/health >/dev/null
install -d -m 0700 "$RELEASE"
tar -xzf "$ARCHIVE" -C "$RELEASE"
tar -czf "$RELEASE/pre-about-web-source.tar.gz" -C "$APP" frontend
docker tag atlas-web:latest atlas-web:pre-about-20261003
docker build --pull=false --build-arg BASE_IMAGE=atlas-web:pre-about-20261003 -f "$RELEASE/ops/voice-rollout/frontend.Dockerfile" -t atlas-web:about-20261003 "$RELEASE"
docker run --rm --network none atlas-web:about-20261003 nginx -t
monitor_active=0
if systemctl is-active --quiet atlas-monitor.timer; then
 monitor_active=1
 systemctl stop atlas-monitor.timer
fi
cleanup() {
 if (( monitor_active )); then systemctl start atlas-monitor.timer; fi
}
trap cleanup EXIT
cp -a "$RELEASE/frontend/src/." "$APP/frontend/src/"
cp "$RELEASE/frontend/nginx.conf" "$APP/frontend/nginx.conf"
cp "$RELEASE/frontend/about.html" "$APP/frontend/about.html"
cp "$RELEASE/frontend/vite.config.ts" "$APP/frontend/vite.config.ts"
docker tag atlas-web:about-20261003 atlas-web:latest
"${PROJECT[@]}" up -d --no-build --no-deps --force-recreate web
healthy=0
for attempt in {1..15}; do
 if curl --fail --silent --max-time 5 https://atlas.prism-news.xyz/api/health >/dev/null && curl --fail --silent --max-time 5 https://atlas.prism-news.xyz/about > "$RELEASE/public-about.html"; then
  healthy=1
  break
 fi
 sleep 1
done
if (( ! healthy )); then
 docker tag atlas-web:pre-about-20261003 atlas-web:latest
 "${PROJECT[@]}" up -d --no-build --no-deps --force-recreate web
 echo 'FAIL: previous web image restored; source backup preserved'
 exit 1
fi
grep -q 'Atlas — голос' "$RELEASE/public-about.html"
[[ "$(curl --silent --output /dev/null --max-time 10 --write-out '%{http_code}' https://atlas.prism-news.xyz/api/model-settings)" == 401 ]]
"${PROJECT[@]}" ps web --format 'table {{.Name}}\t{{.Status}}'
echo 'PASS: public about page; owner API still authenticated; only web recreated'
