# Архитектура
## Границы
Модульный монолит Python. API принимает команды и сохраняет их транзакционно; worker читает очередь и вызывает ModelRouter. Web — только клиент общего API. Telegram/desktop адаптеры используют общие Task/Approval/сервисы. Scheduler работает отдельным процессом этого же пакета.

Текущий путь: web → authenticated API → Message + Task + Audit (одна транзакция) → worker claim → bounded context + memory retrieval → model → Task + assistant Message + Audit (одна транзакция) → WebSocket snapshot → web.

Модели SQLAlchemy: Conversation, Message, Memory, Task, Audit. UUID ключи, timestamps UTC. Memory имеет категорию, owner source, confidence=1, access=owner и confirmed_at. Факты вносит владелец: модель не создаёт биографию автоматически. История ответов сохраняет собственные сообщения агента.

## Восстановление
БД — источник истины; Redis не единственная очередь. Conditional UPDATE получает claim и lease 120s; модель имеет HTTP timeout 60s. Просроченный RUNNING повторяется. Финализация проверяет claim/status, assistant task_id уникален. Повторная генерация может стоить денег, но не публикует второй ответ. В этом этапе внешних побочных действий нет. Не применять этот алгоритм к платежам/отправке/файловым изменениям: этап 2 добавит журнал шагов и reconciliation.

Обычный чат сохраняет прежний путь генерации ответа. Инструментальные задачи используют все состояния PENDING/PLANNING/RUNNING/WAITING_APPROVAL/PAUSED/COMPLETED/FAILED/CANCELLED. WebSocket передаёт состояния раз в секунду; генерация пока не потоковая. Отмена блокирует публикацию, но не останавливает уже выполняющийся HTTP вызов.

## Контекст
Последние 12 сообщений до задачи и до 6 воспоминаний, совпавших по словам. На этапе 6: embedding provider, pgvector, hybrid retrieval, relations, конфликтующие факты и подтверждение обновлений. Extension vector создаётся migration, embedding columns пока нет.

## Безопасность
Single owner. Пароль только из окружения, constant-time hash comparison, подписанная HttpOnly/SameSite=Strict cookie 24h, secure flag в TLS окружении. Запись требует X-Atlas-Client, CORS не разрешён; WebSocket проверяет cookie и точный Origin. Владелец — общая область данных, tenant isolation не нужна.

Секреты модели только конфигурация backend. Они не возвращаются API и не логируются при ошибках. Memory фильтрует типичные паттерны секретов; фильтр не гарантирует обнаружение всех секретов. Модель сейчас не имеет инструментов, поэтому prompt injection не даёт доступ к ОС. Текст модели отображается React без HTML/Markdown исполнения.

Лимитер login локален одному API процессу; не распределён. Logout удаляет cookie, но украденная подписанная cookie остаётся валидна до TTL: полноценное server-side revocation — будущая работа.

## Запланированные контракты
Tool: manifest, permission, risk, idempotency class, dry_run, execute, verify. Запись tool_call до execute; UNKNOWN после crash не повторяет irreversible action без проверки.
Initiative: reason/events/memories/priority/message/channel/state/reply/repeats/next_allowed/expires_at. Outbox unique event_key связывает каналы; silence не означает прочтение.
DiaryProvider: schedule/homework/grades/session_status; external_id + provider уникальны, content hash создаёт change event. Demo явно маркируется.
Desktop: outbound защищённое соединение, device identity, grants с expiry и локальным allowlist. Никакого произвольного shell из модели.

## Первичные справочники
[FastAPI WebSockets](https://fastapi.tiangolo.com/advanced/websockets/) и [SQLAlchemy Session Basics](https://docs.sqlalchemy.org/en/20/orm/session_basics.html). Для каждой операции используется отдельная Session; она не разделяется между потоками.

## Этап 2 — реализованный путь инструментов
`POST /api/tasks` принимает цель, optional plan, dry_run, max_steps и идемпотентный request_key. Fingerprint покрывает исходный запрос до модельного планирования. Планы/аргументы валидируются по каталогу, максимум 8 шагов.
Worker получает conditional claim/lease. LangGraph вызывает plan → execute → finalize. Каждая итерация выполняет один шаг, затем освобождает задачу в PENDING; current_step, call и result — SQL checkpoints. Собственная SQL persistence предотвращает рассогласование эффекта заметки и checkpoint.

ToolCall PREPARED и audit фиксируются до действия. Блокировка task row и проверка claim предотвращают исполнение после отмены/паузы. Policy: explicit deny имеет приоритет; notes.create требует scoped одноразовый approval даже при allowed grant. Approval связан с immutable call_id/arguments, после успеха CONSUMED. Отзыв grants повторно проверяется перед execute. При отмене pending approvals закрываются.

Builtins: memory.search (read), notes.create (atomic DB write). Последний может использовать `$previous` — JSON предыдущего результата как содержимое заметки, без исполнения текста. Note имеет unique call_id; фактическое состояние заметки проверяется до commit. В той же транзакции фиксируются эффект, verified result, consumed approval и продвижение шага.

Dry Run сохраняет только preview, не читает память через tool и не создаёт заметку. Read/atomic ошибки после rollback безопасно повторяются до 3 попыток. STARTED/UNKNOWN из незавершённого non-atomic действия не повторяется: PAUSED с reconciliation_required. Внешние/non-atomic execution modes пока запрещены полностью. Для новых внешних инструментов обязательны verify/reconcile и durable idempotency, не переиспользовать atomic policy без доказательства.

[LangGraph StateGraph reference](https://reference.langchain.com/python/langgraph/graph/state/StateGraph). Граф компилируется перед invoke, но durable authority остаётся SQL, а не память Python процесса.

Миграция 0003 создаёт стабильные policy rows для builtins, не изменяя уже выданные или отозванные grants. Execute держит row lock этого правила до commit; отзыв упорядочивается с выполнением. Отсутствующее правило fail-closed, а флаг allowed не заменяет per-call approval для записи.

## Desktop — этап 5
Owner API Device policy → desktop.read → scoped approval → DeviceCommand/WAITING_DEVICE (атомарный intent) → outbound mTLS poll/permit → локальная file boundary → certificate-bound result → verified ToolCall/PENDING → обычная финализация LangGraph. Gateway проверяет сертификат на уровне TLS, HTTP headers не определяют identity. DeviceMutex сериализует изменения device grants/cancel и доставку/приём result, tool grant row lock держится до commit. Device client не подключается к общей owner cookie и не получает session secret. Не исполняет shell, arbitrary executable или mutations. Настройка и границы: DESKTOP.md.
