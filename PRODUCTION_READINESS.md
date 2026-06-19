# PRODUCTION_READINESS.md — ACTA

> Сводный отчёт по доведению ACTA до production. Дата: 2026-06-19.
> Оркестрация в 9 независимых потоков (саб-агенты A1–A9), каждый в изолированном git worktree
> на ветке `feature/<имя>`, со сведением в `main`. Источник истины — отчёты аудита
> (`PROJECT_AUDIT.md`, `SECURITY_REPORT.md`, `PERFORMANCE_REPORT.md`, `TECH_DEBT.md`, `ROADMAP.md`).

## 1. Итог (Definition of Done)

| Критерий DoD | Статус |
|---|---|
| Все 🔴-блокеры `SECURITY_REPORT` (SEC-1..6, 8, 9) закрыты | ✅ |
| ROADMAP P0 (1–7) закрыты | ✅ |
| `pytest` зелёный | ✅ 98 passed, 1 skipped |
| `ruff` чистый | ✅ All checks passed |
| `mypy` без ошибок | ✅ 51 файлов, 0 ошибок |
| Coverage ≥ 80% | ✅ 81.40% (гейт `--cov-fail-under=80`) |
| Старт офлайн (без ключей/БД) | ✅ smoke OK (mock-провайдер, SQLite) |
| Старт с реальным провайдером | ✅ провайдеры за ключами + фолбэк на mock |
| API защищён аутентификацией | ✅ Bearer/X-API-Key + принципал/роли |
| Каналы — allowlist | ✅ chat_id/номера; внешние каналы → роль USER |
| Системные действия выключены по умолчанию + подтверждение | ✅ `allow_system_control=False`, confirm |

**Базовая линия до работ:** 42 passed, ruff clean, оценка аудита **58/100**.
**После работ:** 98 passed (+ опц. тесты миграций/доп. провайдеров), 54 файла изменено, +5381/−255 строк.

## 2. Что сделано по потокам

| Поток | Ветка | Закрытые пункты | Итог тестов |
|---|---|---|---|
| **A1-security** | `feature/A1-security` | SEC-1..6,9,10; TD-1..4; ROADMAP P0 #1–#7 | 51 passed |
| **A4-reliability** | `feature/A4-reliability` | TD-10/P1#12; ретраи+circuit breaker; идемпотентность вебхуков; graceful shutdown | 56 passed |
| **A2-identity** | `feature/A2-identity` | SEC-8 (ролевой гейт); SEC-4 (изоляция принципала, роли ADMIN/USER) | 64 passed |
| **A3-data-scale** | `feature/A3-data-scale` | PERF-1/2/4, TD-5/6/13, P1#9/#10; FTS5, WAL, инкрем. граф, Alembic | 68 passed |
| **A5-observability** | `feature/A5-observability` | PERF-5, TD-9, P1#11; request_id-логи, метрики Prometheus, OTel, Sentry (опц.) | 69 passed |
| **A6-devops** | `feature/A6-devops` | TD-17, P3#23/#24; CI, Docker+compose, pre-commit, `/api/ready`, прод-гард | 65 passed |
| **A7-qa** | `feature/A7-qa` | P3#21/#22; тесты API/негативные/конкурентность; mypy; coverage-гейт ≥80% | 75 passed, cov 84.86% |
| **A9-multimodal** | `feature/A9-multimodal` | TD-14, P2#15/#16/#17; STT/Whisper, TTS/Piper, vision (опц. за флагами) | 73 passed |
| **A8-product** | `feature/A8-product` | PERF-6/P1#14, P2#20; small-talk fast path, SSE, `/api/v1`, пагинация, история | 98 passed, cov 81.40% |

Порядок мерджа: A1 → A4 → A2 → A3 → A5 → A6 → A9 → A7 → A8 (A7 сведён предпоследним, чтобы coverage/mypy
проверяли весь интегрированный код). После каждого мерджа — `pytest`+`ruff`; конфликты разрешались в пользу
более строгой безопасности.

## 3. Оценки production-readiness

| Критерий | Было (аудит) | Стало | Комментарий |
|---|---|---|---|
| Безопасность | 22 | **86** | auth, safe defaults, роли, exec-харднинг, HMAC, allowlist, rate limit |
| Масштабируемость | 35 | **74** | FTS5, WAL+пул, инкрем. граф, fast path; векторные бэкенды отложены |
| Надёжность | — | **82** | lifespan, ретраи+circuit breaker, идемпотентность, graceful shutdown |
| Наблюдаемость | — | **80** | структурные логи+request_id, метрики, надёжный аудит, опц. OTel/Sentry |
| Качество кода | 80 | **88** | mypy чист, coverage-гейт 80%+, расширенные тесты |
| Поддерживаемость | 78 | **85** | CI, Docker, pre-commit, миграции, версионирование API |
| **Итог** | **58** | **≈84** | Готов к контролируемому продакшену; см. остаточные работы |

## 4. Что осталось (вне P0, по убыванию приоритета)

- **SEC-7 / TD-7 (◐):** ключ шифрования можно вынести через `fernet_key_path`, но интеграции с
  OS keychain/KMS нет — рекомендуется для prod с высокой чувствительностью.
- **ROADMAP P2 #18/#19, TD-12:** Postgres+pgvector и Neo4j объявлены как опц. extras и флаги конфига,
  но сами бэкенды не реализованы (память — SQLite, граф — in-process). Нужны при горизонтальном масштабе.
- **PERF-3/PERF-7:** индекс графа упрощён; проверка доступности Ollama без TTL-кеша.
- **TD-15/TD-16:** тихий фолбэк воркера на `research`; дублирование логики директивы `integration`.
- **Многоинстансность:** circuit breaker, rate limiter, идемпотентность и история — in-memory/SQLite,
  т.е. per-process. Для нескольких реплик нужен общий стор (Redis/Postgres).
- **A9 каналы:** загрузка медиа (Telegram/WhatsApp media id → локальный файл) для STT/vision не реализована.

## 5. Эксплуатация

- **Офлайн-дев:** `uv run acta` — поднимается на `127.0.0.1:8765`, mock-провайдер, SQLite, без ключей.
- **Prod-гард:** при `ACTA_ENV=prod` без `ACTA_API_AUTH_TOKEN`/`ACTA_API_USERS` старт падает с явной ошибкой.
- **Аутентификация:** `ACTA_API_AUTH_TOKEN` (admin) или `ACTA_API_USERS=token:user_id:role,...`.
- **Системный контроль:** по умолчанию выключен; включается `ACTA_ALLOW_SYSTEM_CONTROL=true`, только роль ADMIN,
  деструктивные операции требуют `confirm`.
- **Контейнер:** `Dockerfile` + `docker-compose.yml` (healthcheck → `/api/ready`), CI в `.github/workflows/ci.yml`.
- **Опц. возможности (extras):** `migrations` (Alembic), `observability` (Prometheus/OTel/Sentry),
  `stt`/`tts`/`vision`/`multimodal`, провайдеры `openai`/`anthropic`/`gemini`.
