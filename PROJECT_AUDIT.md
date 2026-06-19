# PROJECT_AUDIT.md — ACTA GHOST

> Повторный аудит репозитория после выполнения плана production-готовности (саб-агенты A1–A9).
> Каждое заключение подтверждено ссылками на код (`путь:строка`). Дата: 2026-06-19 (re-audit).
> Версия: `0.1.0` (`pyproject.toml:3`). Базовая линия прошлого аудита: **58/100**.

## 1. Что это за проект

**ACTA GHOST — Autonomous Cognitive Task Assistant** (`pyproject.toml:4`): персональная когнитивная
платформа на Python/FastAPI. Запрос пользователя (веб-UI / Telegram / WhatsApp) проходит конвейер из
~15 агентов: намерение → контекст → рассуждение → план → стратегия → исполнение воркерами → ответ.
Память шифрована (SQLite+Fernet), граф знаний (networkx), мультимодельный роутинг с офлайн-фолбэком,
трёхъязычность ru/he/en, опциональный полный контроль над ОС (по умолчанию выключен).

## 2. Технологический стек (обновлён)

| Слой | Технологии | Где |
|---|---|---|
| Язык / Web | Python ≥3.11, FastAPI, Uvicorn | `acta/api/app.py` |
| Идентичность | токены→принципалы, роли ADMIN/USER | `acta/identity/` |
| Наблюдаемость | Prometheus, OpenTelemetry, Sentry (опц.) | `acta/observability/runtime.py` |
| Хранилище | SQLite (WAL, per-thread conn, FTS5) | `acta/memory/store.py` |
| Миграции | Alembic | `alembic/`, `alembic.ini` |
| Шифрование | Fernet/PBKDF2, внешний путь ключа | `acta/security/crypto.py` |
| Граф знаний | networkx + инкрементальная персистентность | `acta/knowledge_graph/graph.py` |
| Провайдеры | OpenAI/Anthropic/Gemini/Ollama/Mock + ретраи+circuit breaker | `acta/providers/` |
| Мультимодальность | Whisper STT, Piper TTS, vision (опц., за флагами) | `acta/multimodal/`, `config.py:78-93` |
| Каналы | Telegram/WhatsApp + allowlist + HMAC + дедуп | `acta/channels/` |
| DevOps | Docker, docker-compose, CI (GitHub Actions), pre-commit | корень, `.github/workflows/ci.yml` |

## 3. Что изменилось с прошлого аудита (проверено в коде)

| Прошлый риск | Статус | Доказательство |
|---|---|---|
| SEC-1 RCE без auth | ✅ закрыт | `_require_api_auth` на всех маршрутах (`api/app.py:125,301,343,489,508`) |
| Опасный дефолт system control | ✅ закрыт | `allow_system_control=False` (`config.py:102`) |
| SEC-2 каналы без allowlist | ✅ закрыт | `_is_sender_allowed` (`telegram.py:129`, `whatsapp.py:133`) |
| SEC-3 подпись WhatsApp | ✅ закрыт | `verify_signature` HMAC (`whatsapp.py:120`), вызов в `api/app.py:510-514` |
| SEC-4 утечка данных | ✅ закрыт | изоляция по принципалу (`_resolve_effective_user_id`, `api/app.py:136`) |
| SEC-5 command/env injection | ✅ закрыт | `shlex`+argv без shell, env-allowlist (`system.py:271-295`) |
| SEC-6 неогранич. fs | ✅ смягчён | sandbox для delete/move (`system.py:211-218,297-308`) |
| SEC-7 ключ рядом с БД | ◐ частично | `fernet_key_path` (`crypto.py:59-62`), но KMS/keychain нет |
| SEC-8 декоративные права | ✅ закрыт | ролевой гейт system.control (`permissions.py:64-66`) |
| SEC-9 нет rate limit/CORS | ✅ закрыт | middleware (`api/app.py:52-101,225-233`) |
| PERF-1 поиск памяти O(N) | ✅ закрыт | FTS5+bm25+TF-IDF реранк (`store.py:236-258`) |
| PERF-2 перезапись графа | ✅ закрыт | журнал+компакция (`config.py:130-131`, `graph.py`) |
| PERF-4 один коннект+lock | ✅ закрыт | WAL + per-thread conn (`store.py:73-88`) |
| PERF-5 течь буфера аудита | ✅ закрыт | `deque(maxlen)` + ротация (`audit.py:21,38-46`) |
| TD-10 `on_event` устарел | ✅ закрыт | `lifespan` (`api/app.py:191-217`) |

## 4. Остаточные находки (проверено)

- **RES-1 (Low)**: `principal_role` по умолчанию «admin» при отсутствии (`specialized.py:118`,
  `permissions.py:65`). API и каналы всегда задают роль (`api/app.py:155`, `channels/base.py:69`),
  поэтому эксплуатируется лишь внутренними/тестовыми вызовами. Безопаснее дефолт «user».
- **RES-2 (Medium, масштаб)**: rate limiter, circuit breaker, дедуп вебхуков и история — in-memory/SQLite,
  т.е. per-process (`api/app.py:72-101`, `channels/base.py:16`). Для нескольких реплик нужен общий стор.
- **RES-3 (Medium)**: Postgres+pgvector и Neo4j заявлены в extras/конфиге (`pyproject.toml:31-32`,
  `config.py:122-128`), но бэкенды не реализованы — память SQLite, граф in-process (TD-12).
- **RES-4 (Low)**: загрузка медиа из Telegram/WhatsApp (media_id → файл) для STT/vision не реализована.
- **RES-5 (Low)**: `/metrics` намеренно без аутентификации (`api/app.py:266-270`) — закрыть на уровне сети.

## 5. Состояние сборки (проверено)

- Тесты: **98 passed, 1 skipped** (`uv run pytest -q`).
- Покрытие: **81.40%**, гейт `--cov-fail-under=80` (`pyproject.toml:64`).
- Линтер: **ruff — All checks passed**; mypy сконфигурирован (`pyproject.toml:70-78`).
- Прод-гард: при `ACTA_ENV=prod` без токена старт падает (`config.py:162-165`).

## 6. Итоговые оценки

| Критерий | Прошлый аудит | Текущий | Комментарий |
|---|---|---|---|
| Архитектура | 82 | 86 | идентичность, наблюдаемость, чистый слой API v1 |
| Качество кода | 80 | 88 | mypy, coverage-гейт, расширенные тесты |
| Безопасность | 22 | **84** | auth+роли, харднинг exec, HMAC, allowlist, rate limit |
| Масштабируемость | 35 | 72 | FTS5, WAL, инкрем. граф; векторные/мульти-инстанс отложены |
| Надёжность | — | 82 | lifespan, ретраи+circuit breaker, идемпотентность |
| Наблюдаемость | — | 80 | request_id-логи, метрики, надёжный аудит |
| Поддерживаемость | 78 | 85 | CI, Docker, pre-commit, миграции |
| **Итог** | **58** | **≈83** | Готов к контролируемому продакшену; остаются RES-1..5 |

Детали — в `SECURITY_REPORT.md`, `PERFORMANCE_REPORT.md`, `ARCHITECTURE_REPORT.md`, `FEATURES_MAP.md`,
`TECH_DEBT.md`, `ROADMAP.md`, и сводный `PRODUCTION_READINESS.md`.
