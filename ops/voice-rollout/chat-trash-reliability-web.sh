#!/usr/bin/env bash
# Narrow web-only release for reliable conversation trashing.
set -Eeuo pipefail
umask 077

APP=/opt/atlas/app
RELEASE=/opt/atlas/staging/chat-trash-reliability-20261003
ARCHIVE=/opt/atlas/staging/atlas-chat-trash-reliability-20261003.tar.gz
BASE_IMAGE=atlas-web:pre-chat-trash-reliability-20261003
NEW_IMAGE=atlas-web:chat-trash-reliability-20261003
PROJECT=(docker compose --project-name atlas --file "$APP/compose.yaml" --file "$APP/compose.production.yaml" --file "$APP/ops/desktop-gateway.compose.yaml")

[[ "${1:-}" =~ ^[a-fA-F0-9]{64}$ ]] || exit 1
[[ "$(sha256sum "$ARCHIVE" | cut -d' ' -f1)" == "${1,,}" ]] || exit 1
[[ ! -e "$RELEASE" ]] || { echo 'Release already exists'; exit 1; }
! docker image inspect "$BASE_IMAGE" >/dev/null 2>&1 || { echo 'Previous-image tag already exists'; exit 1; }
"${PROJECT[@]}" config --quiet
curl --fail --silent --max-time 10 https://atlas.prism-news.xyz/api/health >/dev/null

install -d -m 0700 "$RELEASE"
tar -xzf "$ARCHIVE" -C "$RELEASE"
tar -czf "$RELEASE/pre-chat-trash-reliability-source.tar.gz" -C "$APP" \
  frontend/src/ConversationSidebar.tsx frontend/nginx.conf frontend/dist
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
    echo 'FAIL: previous web image restored'
  fi
  if (( monitor_active )); then systemctl start atlas-monitor.timer; fi
  exit "$status"
}
trap finish EXIT

cp "$RELEASE/frontend/src/ConversationSidebar.tsx" "$APP/frontend/src/ConversationSidebar.tsx"
cp "$RELEASE/frontend/e2e/conversations.spec.ts" "$APP/frontend/e2e/conversations.spec.ts"
docker tag "$NEW_IMAGE" atlas-web:latest
web_changed=1
"${PROJECT[@]}" up -d --no-build --no-deps --force-recreate web

healthy=0
for attempt in {1..15}; do
  if curl --fail --silent --max-time 5 https://atlas.prism-news.xyz/api/health >/dev/null && \
     curl --fail --silent --max-time 5 https://atlas.prism-news.xyz/ > "$RELEASE/public-index.html"; then
    healthy=1
    break
  fi
  sleep 1
done
(( healthy )) || { echo 'FAIL: public health'; exit 1; }
asset=$(grep -oE 'assets/app-[^" ]+\.js' "$RELEASE/public-index.html" | head -n1)
[[ -n "$asset" ]]
curl --fail --silent --max-time 10 "https://atlas.prism-news.xyz/$asset" | grep -Fq 'Связь прервалась, и Atlas не смог проверить результат'
[[ "$(curl --silent --output /dev/null --max-time 10 --write-out '%{http_code}' https://atlas.prism-news.xyz/api/conversations)" == 401 ]]
"${PROJECT[@]}" ps web api --format 'table {{.Name}}\t{{.Status}}'
echo 'PASS: chat-trash reliability UI deployed; only web recreated; owner API remains authenticated'
