#!/usr/bin/env bash
# Только интерфейс настроек; БД, backend и модельные профили не меняются.
set -Eeuo pipefail
umask 077
APP=/opt/atlas/app
RELEASE=/opt/atlas/staging/voice-settings-20261002
ARCHIVE=/opt/atlas/staging/atlas-voice-settings-20261002.tar.gz
PROJECT=(docker compose --project-name atlas --file "$APP/compose.yaml" --file "$APP/compose.production.yaml" --file "$APP/ops/desktop-gateway.compose.yaml")
[[ "${1:-}" =~ ^[a-fA-F0-9]{64}$ ]] || exit 1
[[ "$(sha256sum "$ARCHIVE" | cut -d' ' -f1)" == "${1,,}" ]] || exit 1
[[ ! -e "$RELEASE" ]] || { echo 'Release already exists'; exit 1; }
"${PROJECT[@]}" config --quiet
curl --fail --silent --max-time 10 https://atlas.prism-news.xyz/api/health >/dev/null
install -d -m 0700 "$RELEASE"
tar -xzf "$ARCHIVE" -C "$RELEASE"
tar -czf "$RELEASE/pre-settings-web-source.tar.gz" -C "$APP" frontend/src/main.tsx frontend/src/ModelSettingsPanel.tsx frontend/nginx.conf
docker tag atlas-web:latest atlas-web:pre-settings-20261002
docker build --pull=false --build-arg BASE_IMAGE=atlas-web:pre-settings-20261002 -f "$RELEASE/ops/voice-rollout/frontend.Dockerfile" -t atlas-web:settings-20261002 "$RELEASE"
monitor_active=0
if systemctl is-active --quiet atlas-monitor.timer; then
    monitor_active=1
    systemctl stop atlas-monitor.timer
fi
cleanup() {
    if (( monitor_active )); then systemctl start atlas-monitor.timer; fi
}
trap cleanup EXIT
cp "$RELEASE/frontend/src/main.tsx" "$APP/frontend/src/main.tsx"
cp "$RELEASE/frontend/src/ModelSettingsPanel.tsx" "$APP/frontend/src/ModelSettingsPanel.tsx"
docker tag atlas-web:settings-20261002 atlas-web:latest
"${PROJECT[@]}" up -d --no-build --no-deps --force-recreate web
healthy=0
for attempt in {1..15}; do
    if curl --fail --silent --max-time 5 https://atlas.prism-news.xyz/api/health >/dev/null; then
        healthy=1
        break
    fi
    sleep 1
done
(( healthy )) || { echo 'FAIL: HTTPS health; previous web image preserved'; exit 1; }
"${PROJECT[@]}" ps web --format 'table {{.Name}}\t{{.Status}}'
echo 'PASS: web settings updated; backend, database and selected models unchanged'
