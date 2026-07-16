# Workspace Depth V2

Этот документ описывает реализованную в ветке `feature/workspace-depth-v2`
систему глубокого рабочего пространства. Ветка не должна попадать в production
без review, резервной копии и прохождения release gate.

## Что входит

1. Модульная frontend-оболочка, event bus, hash deep links и feature flags.
2. Realtime messaging controls и Chat Anywhere — перемещаемое, dockable,
   resizable и сворачиваемое окно на любой странице.
3. Защищённый Asset Viewer для изображений, видео, аудио, PDF, текстов и таблиц.
4. Версии файлов, сравнение, contextual annotations, review и approval.
5. Живая карта сущностей проекта и редактор блок-схем.
6. Decision Intelligence: решения, обязательства, действия, риски, вопросы,
   приоритетная очередь и управленческие сводки.

## Архитектура frontend

Точка входа `web/workspace-depth.js` запускает независимые модули только после
получения публичных feature flags из `/api/auth/config`. Старый `web/app.js`
остаётся bridge-слоем и предоставляет модулям только `api`, `navigate`, `notify`
и безопасный snapshot текущего workspace/project/user.

Связь модулей выполняется через `web/core/event-bus.js`; deep links — через
`web/core/context-router.js`. Контекст меняется событием `aap:context-change`,
а серверные изменения приходят по project-scoped SSE endpoint. Модули не
обращаются к внутреннему state legacy-приложения напрямую.

## Feature flags

```env
YT_LOADER_FEATURE_WORKSPACE_DEPTH_SHELL=true
YT_LOADER_FEATURE_CHAT_ANYWHERE=true
YT_LOADER_FEATURE_ASSET_VIEWER=true
YT_LOADER_FEATURE_ASSET_REVIEWS=true
YT_LOADER_FEATURE_PROJECT_GRAPH=true
YT_LOADER_FEATURE_DECISION_INTELLIGENCE=true
```

Все флаги по умолчанию выключены. Рекомендуемый rollout: shell → chat → viewer →
reviews → graph → decision intelligence. После каждого включения проверить
readiness, ошибки API, desktop/mobile UX и tenant isolation двумя аккаунтами.

## Миграции

Schema-цепочка Workspace Depth V2:

- `h3c4d5e6f7a8` — realtime controls сообщений;
- `i4d5e6f7a8b9` — версии, review и approval;
- `j5e6f7a8b9c0` — граф сущностей и блок-схемы;
- `k6f7a8b9c0d1` — insights, impact links и briefing.

Контейнер `migrate` выполняет `alembic upgrade head` до старта приложения.
До production-миграции обязательны backup PostgreSQL и `server_data`, проверка
восстановления и пробный upgrade/downgrade на копии базы.

## Основные API

- `/api/projects/{id}/message-events` — project-scoped SSE;
- `/api/content-attachments/{id}/preview` — авторизованный preview/stream;
- `/api/content-attachments/{id}/versions` — история и загрузка версии;
- `/api/content-attachments/{id}/reviews` — contextual review;
- `/api/content-attachments/{id}/approval` — решения по версии;
- `/api/projects/{id}/graph` и `/api/projects/{id}/entity-links` — граф;
- `/api/projects/{id}/diagrams` — блок-схемы;
- `/api/projects/{id}/insights` — сигналы и ручная фиксация;
- `/api/projects/{id}/insights/extract` — rule/optional AI extraction;
- `/api/projects/{id}/attention` — агрегированная очередь внимания;
- `/api/projects/{id}/briefings` — управленческие сводки.

## Безопасность и приватность

- Каждый endpoint повторно проверяет membership проекта; ID сущности не является
  доказательством доступа.
- Физические пути и произвольные локальные файлы не передаются браузеру.
- Приватные direct/group conversations видят только их участники; граф применяет
  ту же проверку.
- Автоматический Decision Intelligence читает только conversation с
  `is_project_wide=true`. Личные и закрытые групповые чаты исключены и из rule,
  и из AI-контекста.
- Роль client видит только review/insight/briefing с `visibility=client`.
- AI запускается явно и получает ограниченный JSON-контекст. Невалидный или
  чрезмерный ответ фильтруется; при ошибке остаётся deterministic fallback.
- Поддерживаемые файлы проверяются по расширению, MIME и сигнатуре/структуре до
  сохранения; Content-Disposition и preview headers формируются сервером.

## Локальная проверка

```powershell
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider --basetemp .venv\pytest-release
node --check web/modules/chat-anywhere.js
node --check web/modules/asset-viewer.js
node --check web/modules/asset-reviews.js
node --check web/modules/project-graph.js
node --check web/modules/decision-intelligence.js
$env:POSTGRES_PASSWORD='config-check'; docker compose config -q
```

Подтверждённый результат на 17 июля 2026: `201 passed`, `21 subtests passed`.
Единственное предупреждение — известный deprecation warning связки
Starlette TestClient/httpx. Один первый полный прогон кратковременно получил
SQLite `database is locked` в старом worker-тесте; отдельный повтор и полный
повторный suite прошли успешно.

## Production rollout

1. Review и merge `feature/workspace-depth-v2` в `main` без force/reset.
2. Создать свежий локальный и внешний backup, проверить свободное место.
3. Получить `main` через `git pull --ff-only`, проверить production `.env` и
   `docker compose config --quiet`.
4. Выполнить `docker compose up -d --build`; убедиться, что migrate завершился,
   а `alembic current` показывает `k6f7a8b9c0d1`.
5. Сначала оставить новые флаги выключенными и выполнить health/readiness.
6. Включать флаги по одному в указанном порядке с smoke/E2E после каждого.
7. При ошибке выключить конкретный flag. Downgrade базы выполнять только после
   остановки приложения и с проверенным backup; обычный rollback UI не требует
   удаления новых таблиц.

Production-развёртывание в рамках реализации Workspace Depth V2 не выполнялось.
