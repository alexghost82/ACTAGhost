# ARCHITECTURE_REPORT.md — ACTA

## Точки входа
- `acta` → `acta.api.app:run` — FastAPI на `127.0.0.1:8765` (`api/app.py:180-185`).
- `acta-telegram` → `channels.telegram:run_cli` — отдельный Telegram-поллер (`telegram.py:97`).
- `acta.api.app:app` — ASGI-приложение (`api/app.py:177`).

## Слои
1. **Каналы** (`acta/channels/`): Telegram (поллинг/вебхук), WhatsApp (вебхук Cloud API), `ChannelHub`
   нормализует вход в `UserRequest` с `user_id = "<channel>:<sender_id>"` (`channels/base.py:22-25`).
2. **API** (`acta/api/app.py`): REST (`/api/chat`, `/api/status`, `/api/agents`, `/api/memory`,
   `/api/audit`, `/api/channels`), вебхуки, статика веб-UI.
3. **Оркестратор** (`acta/orchestrator/`): владеет жизненным циклом агентов, гонит `PipelineState`
   через 15 шагов (`orchestrator.py:63-119`), исполняет план воркерами (seq/parallel).
4. **Агенты** (`acta/agents/`): когнитивные (intent, context, reasoning, planning, decision, memory, kg,
   integration, security, multimodal, ui) + воркеры (research, coding, automation, system).
5. **Провайдеры** (`acta/providers/`): `AIRouter` с правилами профиль→провайдер и фолбэком на `mock`.
6. **Память** (`acta/memory/`): шифрованный SQLite + лексический TF-IDF поиск.
7. **Граф знаний** (`acta/knowledge_graph/`): `networkx.MultiDiGraph` с JSON-персистентностью.
8. **Интеграции** (`acta/integration/`): echo, http, sandbox-fs, **system (полный контроль ОС)**.
9. **Безопасность** (`acta/security/`): crypto (Fernet), permissions (capability-карта), audit.

## Поток данных
`UserRequest` → `PipelineState` (мутабельный, `state.py:26-41`) ← читают/пишут все агенты →
`ActaResponse` (`state.py:54-64`) с `answer`, `intent`, `plan`, `strategy`, `trace`, `artifacts`.

Паттерн вдохновлён LangGraph: единый shared-state-объект, узлы=агенты (`state.py:1-6`).

## Поток аутентификации
**Отсутствует.** Нет идентификации/авторизации пользователей; `user_id` берётся как есть из запроса/канала.
Это центральная архитектурная брешь (см. `SECURITY_REPORT.md`).

## Поток БД
`MemoryStore` открывает один `sqlite3.connect(check_same_thread=False)` с `RLock` (`store.py:62-64`).
Контент шифруется перед записью (`store.py:125`), расшифровывается при чтении (`store.py:140`).
Граф знаний сохраняется целиком в JSON при каждом запросе (`knowledge_graph_agent.py:36` → `graph.py:37`).

## Сильные стороны
- Чёткое разделение ответственности, маленькие модули, явные контракты (`schemas.py`).
- Прозрачный, инспектируемый конвейер с трассировкой шагов (`TraceEntry`).
- Деградация без внешних зависимостей (mock-провайдер, локальные хранилища).
- Трёхъязычность (ru/he/en) пронизывает конвейер (`i18n.py`, `multimodal_agent.py:22-24`).

## Архитектурные риски
- Граница доверия не выражена в архитектуре (любой вход = полные права).
- Конвейер строго линейный, 15 шагов на каждый запрос даже для small-talk → накладные расходы.
- Единый SQLite-коннект + глобальный lock ограничивают параллелизм воркеров.
- `@app.on_event("startup")` устарел (`api/app.py:55`) — следует перейти на lifespan.
