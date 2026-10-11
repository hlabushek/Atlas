#!/usr/bin/env bash
# Narrow web-only rollout: retire unfinished initiative/demo-diary UI.
set -Eeuo pipefail
umask 077

APP=/opt/atlas/app
RELEASE=/opt/atlas/staging/demo-initiative-cleanup-r2-20261003
ARCHIVE=/opt/atlas/staging/atlas-demo-initiative-cleanup-r2-20261003.tar.gz
BASE_IMAGE=atlas-web:pre-demo-initiative-cleanup-r2-20261003
NEW_IMAGE=atlas-web:demo-initiative-cleanup-r2-20261003
PROJECT=(docker compose --project-name atlas --file "$APP/compose.yaml" --file "$APP/compose.production.yaml" --file "$APP/ops/desktop-gateway.compose.yaml")

[[ "${1:-}" =~ ^[a-fA-F0-9]{64}$ ]]
[[ "$(sha256sum "$ARCHIVE" | cut -d' ' -f1)" == "${1,,}" ]]
[[ ! -e "$RELEASE" ]]
! docker image inspect "$BASE_IMAGE" >/dev/null 2>&1
"${PROJECT[@]}" config --quiet
curl --fail --silent --max-time 10 https://atlas.prism-news.xyz/api/health >/dev/null

install -d -m 0700 "$RELEASE"
tar -xzf "$ARCHIVE" -C "$RELEASE"
tar -czf "$RELEASE/pre-demo-initiative-cleanup-source.tar.gz" -C "$APP" \
  frontend/src/SkillsPanels.tsx frontend/src/main.tsx frontend/src/SettingsNavigation.tsx \
  frontend/src/types.ts frontend/nginx.conf frontend/dist

docker tag atlas-web:latest "$BASE_IMAGE"
docker build --pull=false --network=none --build-arg BASE_IMAGE="$BASE_IMAGE" \
  -f "$RELEASE/ops/voice-rollout/frontend.Dockerfile" -t "$NEW_IMAGE" "$RELEASE"
docker run --rm --network none "$NEW_IMAGE" nginx -t

monitor_active=0
web_changed=0
if systemctl is-active --quiet atlas-monitor.timer; then
  monitor_active=1
  systemctl stop atlas-monitor.timer
fi
finish() {
  status=$?
  if (( status != 0 && web_changed )); then
    docker tag "$BASE_IMAGE" atlas-web:latest
    "${PROJECT[@]}" up -d --no-build --no-deps --force-recreate web || true
    echo 'FAIL: предыдущий web-образ восстановлен'
  fi
  if (( monitor_active )); then systemctl start atlas-monitor.timer; fi
  exit "$status"
}
trap finish EXIT

cp "$RELEASE/frontend/src/SkillsPanels.tsx" "$APP/frontend/src/SkillsPanels.tsx"
cp "$RELEASE/frontend/src/main.tsx" "$APP/frontend/src/main.tsx"
cp "$RELEASE/frontend/src/SettingsNavigation.tsx" "$APP/frontend/src/SettingsNavigation.tsx"
cp "$RELEASE/frontend/src/types.ts" "$APP/frontend/src/types.ts"
cp "$RELEASE/frontend/nginx.conf" "$APP/frontend/nginx.conf"

docker tag "$NEW_IMAGE" atlas-web:latest
web_changed=1
"${PROJECT[@]}" up -d --no-build --no-deps --force-recreate web

curl --fail --silent --max-time 10 https://atlas.prism-news.xyz/ > "$RELEASE/public-index.html"
asset=$(grep -oE 'assets/app-[^" ]+\.js' "$RELEASE/public-index.html" | head -n1)
[[ -n "$asset" ]]
curl --fail --silent --max-time 10 "https://atlas.prism-news.xyz/$asset" > "$RELEASE/public-app.js"
grep -Fq 'Демо-дневник отключён.' "$RELEASE/public-app.js"
! grep -Fq 'Установить демо-дневник' "$RELEASE/public-app.js"
! grep -Fq 'Создать напоминание' "$RELEASE/public-app.js"
[[ "$(curl --silent --output /dev/null --max-time 10 --write-out '%{http_code}' https://atlas.prism-news.xyz/api/conversations)" == 401 ]]
curl --fail --silent --max-time 10 https://atlas.prism-news.xyz/api/health >/dev/null
"${PROJECT[@]}" ps web api --format 'table {{.Name}}\t{{.Status}}'
echo 'PASS: demo diary/initiative UI removed; only web recreated; authenticated API, backend and models unchanged'
