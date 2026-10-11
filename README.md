# Atlas

Голосовой кластер 3 октября: [Voice Center, настройки и ограничения](docs/VOICE_CENTER.md). Центр установлен в основном интерфейсе; точность и целевые задержки требуют явных проб владельца.
Персональная агентная платформа. **Этапы 0–7 в описанном ниже объёме**, не завершённая реализация всего ТЗ.

Уже есть: React/TypeScript интерфейс с двумя темами, API владельца, сохранённые разговоры, явная долговременная память, поиск/редактирование/удаление/экспорт, очередь ответов в БД, отдельный worker, восстановление истёкших leases, отмена и журнал. Модели: детерминированное demo, Ollama и OpenAI-совместимые chat completions.

Добавлены встроенные инструменты, LangGraph задачи, подтверждения, permissions и pause/resume; scheduler с инициативами, Telegram adapter, управляемые навыки/MCP и демо-дневник. Реальный Telegram требует токена и owner ID, реальный дневник — адаптации к известному сервису. Демо и учебный план не являются LLM. Desktop Companion имеет защищённый read-only контур; добавлены публичный GitHub connector, опциональные embeddings/pgvector, конфликты памяти, текстовые вложения, браузерный голос, PWA/push, бюджет и эксплуатационные проверки. Для сайтов добавлены передача зашифрованной сессии из Chrome на VPS, отдельный браузер Chromium для ручного входа и JavaScript-страниц, ограниченные действия записи с подтверждением и проверкой результата; порядок и границы описаны в `integrations/README.md`. Код интеграций выпущен на VPS 4 октября 2026 года после проверки миграций и восстановления резервной копии. Реальные embeddings/push и доступ к стороннему аккаунту требуют настройки и отдельной проверки.

## Docker Compose (Windows через Docker Desktop/WSL2 или Linux)

Ключи и локальные данные храните только вне Git. Файлы `.env` и `.env.*` (кроме `.env.example`), базы данных, сертификаты, приватные ключи, сессии и локальные вложения исключены через `.gitignore`. Значения в `.env.example` — примеры для локального запуска; перед любым публичным развёртыванием задайте собственные пароли и секреты. Не вставляйте реальные ключи в код, документацию или тесты.

1. Запустите Docker Desktop с Linux engine.
2. Скопируйте `.env.example` в `.env`. Установите уникальный пароль 16+ символов и случайный session secret 32+ символов. Оба обязательны; сервер не работает с пустыми значениями.
3. `docker compose up --build -d`
4. Откройте http://localhost:8080. Миграции выполняются отдельным контейнером до API/worker/scheduler/telegram.
5. `docker compose logs -f api worker scheduler telegram`. Остановка: `docker compose down` (без `-v`, чтобы сохранить данные).

Порты БД/Redis/API не опубликованы; web доступен только на loopback. Для VPS сначала настройте TLS reverse proxy, `ATLAS_COOKIE_SECURE=true`, отдельные PostgreSQL credentials и политику резервного копирования. Текущий Compose рассчитан на локальный запуск.

## Без Docker, PowerShell — проверочный SQLite профиль
```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r backend\requirements.txt
$env:ATLAS_OWNER_PASSWORD = 'ваш-уникальный-пароль-16-символов'
$env:ATLAS_SESSION_SECRET = 'ваша-случайная-строка-минимум-32-символа'
$env:ATLAS_DATABASE_URL = 'sqlite:///./atlas.db'
cd backend
..\.venv\Scripts\alembic.exe upgrade head
..\.venv\Scripts\uvicorn.exe atlas.api:app --host 127.0.0.1 --port 8000
```
Во втором терминале установите те же переменные, перейдите в `backend`, выполните `..\.venv\Scripts\python.exe -m atlas.worker`.
В дополнительном терминале с теми же переменными из `backend`: `..\.venv\Scripts\python.exe -m atlas.scheduler`. Для настроенного бота отдельно: `..\.venv\Scripts\python.exe -m atlas.telegram`.
В следующем: `cd frontend`, `npm ci`, `npm run dev`. Откройте http://127.0.0.1:5173. Весь трафик API идёт через Vite proxy.

## Проверки
Backend из `backend`: `..\.venv\Scripts\python.exe -m pytest -q`.
Frontend из `frontend`: `npm run build`, `npm test`.
E2E: запустите API, worker и scheduler с demo, задайте `$env:ATLAS_E2E_PASSWORD`, затем `npx playwright install chromium` и `npm run test:e2e`. Тест без пароля явно пропускается.
Миграции проверяются отдельно: `alembic upgrade head`, `alembic check`.

Документация: [архитектура](docs/ARCHITECTURE.md), [модели](docs/MODELS.md), [следующие адаптеры](docs/INTEGRATIONS.md), [ограничения](docs/LIMITATIONS.md), [план](PLAN.md), [отчёт](docs/STATUS.md).

## Инструментальная задача (этап 2)
Откройте Активность, введите цель и запустите задачу. Demo ищет память и готовит заметку. Перейдите в Подтверждения, проверьте tool и конкретные параметры, разрешите или отклоните действие. Результат появится в Активности → Заметки; Шаги и результаты показывают сохранённые вызовы. В обычном чате tools не включены автоматически.
Dry Run не вызывает execute и не создаёт заметки/approvals. Инструменты позволяют запретить builtin; этот запрет действует даже после одобрения вызова. Ошибка/UNKNOWN отображается явно, неизвестные эффекты не повторяются.

## PostgreSQL regression и проверка перезапуска
```powershell
docker compose exec -T -e ATLAS_TEST_POSTGRES_URL=postgresql+psycopg://atlas:atlas@postgres:5432/atlas api python -m pytest -q -p no:cacheprovider
docker compose exec -T api alembic check
$env:ATLAS_E2E_URL = 'http://127.0.0.1:8080'
# Задайте ATLAS_E2E_PASSWORD паролем владельца из .env; затем из frontend:
npm run test:e2e
# Из корня проекта, с работающим Compose:
.\.venv\Scripts\python.exe scripts\check_restart.py
```
pytest создаёт уникальную схему и удаляет только её. Нужна отдельная test DB роль с CREATE SCHEMA; текущая локальная atlas роль подходит. Не использовать production credentials в CI. E2E и restart drill создают реальные тестовые записи в demo БД, помеченные E2E/RESTART DRILL; restart drill перезапускает только api и worker этого Compose проекта.

Docker сборка исключает .env и локальные *.db. Зависимости backend фиксируются requirements.lock; frontend — package-lock.json. Перед обновлением локальной БД используйте backup; migration 0002 сохраняет уже существующие разговоры/сообщения/задачи.

## Новые разделы
Инициативы: включите уведомления, задайте часовой пояс/тихие часы/лимит, создайте напоминание. Откройте его разговор и ответьте: повтор прекращается, если связь однозначна. При нескольких активных инициативах в одном разговоре backend принимает явный initiative_id; интерфейс пока его не выбирает.
Telegram: настройте переменные в серверном .env, получите код в вебе и отправьте боту /start CODE от указанного владельца. Секреты не вводите в чат.
Навыки: установите демо-дневник и включите его. В Дневнике разрешите инструменты, синхронизируйте и подтвердите действие в Подтверждениях. Задания demo редактируются отдельно от синхронизированного состояния.
MCP/REST и optional browser adapter: см. docs/INTEGRATIONS.md. Проверка scheduler restart: `.\.venv\Scripts\python.exe scripts\check_channels_restart.py`; создаёт помеченные записи и после проверки выключает инициативы.

## Desktop Companion (этап 5)
Устройства → public certificate fingerprint → явный доступ/aliases/expiry. desktop.read требует grant и отдельного approval; offline команда сохраняется в БД. После подключения Companion проверяет серверное и локальное разрешение, читает файл/каталог и отправляет результат в общий Task. Поддерживается чтение UTF-8 до 64 КБ и list до 100 записей, без shell/записи/запуска программ.
Установка на ПК: `python -m pip install ./backend`, затем `atlas-companion C:/private/atlas/companion.json`. Настройка CA/сертификатов, отдельного mTLS gateway и локальных каталогов — [Desktop руководство](docs/DESKTOP.md). Стандартный Compose не включает gateway без настроенных cert/key/CA.

## Интеграции и эксплуатация (этапы 6/7)
Раздел Интеграции: GitHub public repo через grant/approval, статус semantic provider, факты/конфликты, загрузка файлов, token/cost ledger с месячными и дневными UTC лимитами и лимитом стоимости на задачу, push opt-in и heartbeat процессов. В чате выберите текстовое вложение или продиктуйте черновик. Demo по-прежнему не является LLM.
[Настройка расширенной памяти и каналов](docs/EXTENDED_MEMORY.md), [HTTPS и threat review](docs/OPERATIONS.md), [backup/restore](docs/BACKUP.md). Production overlays не публикуют сервер в интернет; выберите домен/сертификат/внешний proxy и credentials отдельно. После миграции 0008 старые cookies отзываются форматом сессии: снова войдите.
PWA/push проверяются на production build (Compose 8080/HTTPS); Vite 5173 не регистрирует service worker. Для E2E текущего этапа используйте собранный Compose.

## Выбор моделей
Откройте Настройки → Модели Atlas. Основная: DeepSeek V4.1 Flash; сложные задачи: Claude Sonnet 5.5; память: Qwen3-Embedding-8B; готовая альтернатива: Gemini 3.8 Flash. Меняйте provider/model id, каталог или ручной ввод; новые задачи сразу используют сохранённый выбор. В чате есть режим сложной задачи. API keys только в серверных `.env`: ATLAS_ROUTERAI_API_KEY / ATLAS_PLUSVIBE_API_KEY. Без ключей статус «не подключён», демо не подменяет выбранную модель. Детали — docs/MODELS.md.
