# Эксплуатация и проверка безопасности
## Запуск и диагностика
Все миграции аддитивны, запуск только через Alembic migrate; create_all при старте отсутствует. Python версии pinned в backend/requirements.lock, npm — package-lock.json/npm ci. Локальные backend-проверки запускайте проектным окружением: ..\.venv\Scripts\python.exe -m pytest из каталога backend (системный Python может не содержать locked dependencies). Образы Docker имеют версионные base tags, production digest pinning и регулярный CVE review остаются процедурой оператора. pywin32 имеет Windows marker, Linux образ не устанавливает его.
`/api/ops` доступен только владельцу: агрегаты состояний задач/ledger и heartbeat worker/scheduler/telegram. Нет prompts/секретов/host paths. Отсутствующий или старый heartbeat означает, что процесс надо проверить; endpoint не выполняет автоматический restart. `/api/health` — только liveness, не проверка всех зависимостей. Мониторинг/alert manager/tracing/log rotation/SLA не развернуты. Внешние REST/MCP endpoints обязаны использовать HTTPS; HTTP допускается только для loopback fixture (`localhost`, `127.0.0.1`, `::1`).
Для текущего VPS поверх исторической процедуры развернут `ops/monitor/atlas-monitor.py`: systemd timer раз в 5 минут проверяет HTTPS, контейнеры Atlas, таймеры backup/restore и свободное место. При смене состояния он отправляет Telegram владельцу; тестовое сообщение отправлено. Проверки остаются базовым мониторингом, без гарантии SLA и автоматического failover.
Owner сессии сохраняются в БД. Активный WebSocket повторно проверяет сессию перед каждым обновлением и закрывается после отзыва. Logout отзывает текущую сессию, «Завершить все сессии» — все; старые cookies этапа 5 больше не принимаются. Потребуется снова войти. Нет возвращения session secret/подписок API или export. Ошибки Pydantic не повторяют входной payload, включая пароль. API schema/docs endpoints отключены.

## HTTPS
`compose.tls.yaml` включает TLS1.2/1.3, HSTS, Secure/HttpOnly cookies, read-only mounts сертификата и ключа. Задайте ATLAS_TLS_CERT и ATLAS_TLS_KEY абсолютными путями к сертификатам с нужным SAN. Пример: `docker compose -f compose.yaml -f compose.tls.yaml up -d` слушает только loopback 8443. Для нового production deployment также подключите `-f compose.production.yaml`, отдельное Compose project name/volume и обязательные ATLAS_POSTGRES_PASSWORD/ATLAS_DATABASE_URL. Пароли URL кодируйте; owner/session secrets задаются через `.env` и проходят проверку длины.
Production overlay предназначен для **новой БД**: POSTGRES_PASSWORD не меняет пароль уже созданного volume. Существующую БД переносите через проверенный restore и отдельную процедуру смены role credential. Не запускайте новый production profile на старом dev volume с несовпадающими credentials. Dev compose пароль atlas нельзя использовать вне loopback isolated среды.
Сертификат публичного домена/renewal/DNS/edge firewall надо настроить оператору. В интернет ничего не опубликовано. `scripts/check_tls.py` временно проверяет HTTPS overlay с однодневным локальным доверенным сертификатом, Secure cookie и HSTS, затем возвращает HTTP compose. Это настоящий TLS, но не публичный CA/production rollout. Локальные ключи игнорируются в .sessions; ограничьте OS ACL и не переносите их в production.

## Threat review
| Граница | Защита / проверка | Остаток |
|---|---|---|
| Web → API | Auth везде кроме health/login, signed DB session/revoke, SameSite strict, CSRF header, exact WebSocket origin, CSP/nosniff | Single owner; нет MFA; login rate-limit локален процессу и сбрасывается при рестарте |
| Контекст → инструменты | Недоверенный контекст не даёт grants; schema/allowlist/scoped approval; revoke повторно проверяется; shell отсутствует | Prompt injection не исключён для текста модели; внешние записи запрещены |
| Файлы → память/модель | Только bounded UTF-8 текст, secret filter, explicit attach, не исполняется, нет arbitrary filesystem path | Неизвестный секрет без шаблона может не распознаться; ACL/encryption at rest — оператор |
| Remote REST/MCP/GitHub | Allowed hosts/no redirects/timeout, выбранные read-only tools; фиксированный публичный GitHub | Нет general OAuth/private repo/write; provider availability/rate limit |
| Desktop | mTLS/fingerprint, независимые local grants, nofollow/reparse/hardlink checks, durable intent/UNKNOWN; создание новых ограниченных текстовых файлов с отдельным grant/approval и no-overwrite | Нет замены/удаления файлов и запуска программ; нет OS sandbox/production PKI |
| Push | Allowed HTTPS origins + EC keys, VAPID/шифрование, generic payload, no redirect, opt-out/quiet/reply gate, UNKNOWN no replay | Subscription capability хранится в БД: backups приватны; live устройство не проверено |
| Provider → бюджет | Durable reservation до запроса, race test, unknown held, configured tariffs | Usage от провайдера — доверенные метаданные; нет per-task/day cap |
| Restore → БД | Сначала isolated restore, Alembic version + хэши всех таблиц; source equality | Нет automatic disaster failover, RPO/RTO обещаний, backup retention/encryption расписания |

## Проверки A–H и crashes
A: owner чат/reload/история/общая память — backend + E2E. B: два реальных builtin шага, note/approval/trace — backend + E2E. C: rollback atomic write, истёкшая lease, конкурирующие workers, UNKNOWN запрещает replay, scripts/check_restart.py — настоящий API/worker restart. D/E: cross-channel reply, quiet hours, limits/repeats/expiry + scheduler restart; Telegram transport тестовый. F: delete/edit инвалидируют vector/fact/conflict и исключают память из retrieval. G: denied/revoked grants/skills/Desktop с audit. H: demo diary изменение/ревизии/событие/учебная задача, реальные локальные REST/MCP протоколы. A–H проходят в поддержанном объёме; live Telegram/дневник/удалённый Companion остаются отдельной приёмкой.
Дополнительные fault tests: provider timeout сохраняет неизвестный расход; concurrent budget reservation не превышает лимит; push timeout после durable intent не переотправляется. Это локальная crash/fault проверка, не chaos test реального облака.
`python scripts/check_load.py`: 100 authenticated/read запросов, concurrency20, health/tasks/ops/memory, p50/p95/error count в игнорируемом .backups/load-report.json. Без LLM/TG/платных запросов. Результат зависит от Docker Desktop/машины, не capacity guarantee production.

Для полного E2E без обращения к платным провайдерам используйте отдельный Compose-проект:

```powershell
docker compose -p atlas-e2e -f compose.yaml -f compose.e2e.yaml up -d --build
Push-Location frontend
$env:ATLAS_E2E_PASSWORD = (Get-Content ..\.env | Where-Object { $_ -match '^ATLAS_OWNER_PASSWORD=' } | ForEach-Object { $_.Substring(21) })
npm run test:e2e
Remove-Item Env:ATLAS_E2E_PASSWORD
Pop-Location
docker compose -p atlas-e2e -f compose.yaml -f compose.e2e.yaml down -v
```

Профиль принудительно использует demo-провайдер и получает отдельные volumes благодаря имени проекта.

Полный локальный прогон без Docker: `.venv/Scripts/python.exe scripts/check_local_e2e.py` из корня. Скрипт создаёт отдельную SQLite-БД в `.sessions/voice-e2e-*`, применяет Alembic, запускает API/worker/scheduler и Vite на временных loopback-портах, затем выполняет `npm run test:e2e -- --workers=1`. Учетные данные генерируются в окружении дочерних процессов; `.env` рабочей установки не используется. После тестов процессы останавливаются, тестовая база остаётся для разбора. Два теста окна голоса используют подменённый WebView bridge для проверки UI; реальное ASR и Windows-мьютексы проверяются отдельно.
