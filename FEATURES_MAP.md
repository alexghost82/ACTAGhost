# FEATURES_MAP.md — ACTA

Каталог реализованных возможностей. Качество: 1-5 (5 — продакшен-уровень).

| Функция | Описание | Файлы | Ценность | Качество |
|---|---|---|---|---|
| Когнитивный конвейер из 15 шагов | Оркестрация агентов от намерения до ответа | `orchestrator/orchestrator.py` | Ядро продукта | 4 |
| Распознавание намерения | Эвристики по ключам ru/he/en + уточнение моделью | `agents/intent_agent.py` | Высокая | 4 |
| Планирование задач | Декомпозиция целей, выбор воркера, зависимости | `agents/planning_agent.py` | Высокая | 4 |
| Стратегия и роутинг | seq/parallel + профиль→провайдер | `agents/decision_agent.py` | Средняя | 4 |
| Воркеры (research/coding/automation/system) | Исполнение подзадач | `agents/specialized.py` | Высокая | 3 |
| **Полный контроль ОС** | exec/spawn/processes/kill/service/fs/info | `integration/system.py` | Высокая/опасная | 3 |
| Память (эпизод./семант./персон.) | Шифрованный SQLite + TF-IDF поиск | `memory/store.py`, `agents/memory_agent.py` | Высокая | 3 |
| Граф знаний | networkx, рост и поиск связей | `knowledge_graph/graph.py` | Средняя | 3 |
| Мультимодельный роутер | OpenAI/Anthropic/Gemini/Ollama + mock-фолбэк | `providers/` | Высокая | 4 |
| Офлайн mock-провайдер | Детерминированные ответы без ключей | `providers/mock.py` | Высокая (DX/тесты) | 5 |
| Трёхъязычность ru/he/en | Детект по скрипту + директива модели + RTL | `i18n.py`, `multimodal_agent.py` | Высокая | 4 |
| Telegram-канал | Поллинг и вебхук на httpx | `channels/telegram.py` | Средняя | 3 |
| WhatsApp-канал | Cloud API вебхук + отправка | `channels/whatsapp.py` | Средняя | 2 (нет проверки подписи) |
| Веб-UI чат + инспектор | Чат, трасса, план, намерение, статус | `acta/web/` | Высокая (демо) | 4 |
| Шифрование at-rest | Fernet + PBKDF2, ключ 0600 | `security/crypto.py` | Высокая | 4 |
| Аудит действий | Append-only журнал | `security/audit.py` | Средняя | 3 |
| Capability-права | Карта ролей→способности | `security/permissions.py` | Низкая (декоративна) | 2 |
| Мультимодальность вход/выход | Нормализация текста/голоса/изображений | `multimodal/processor.py` | Средняя | 2 (хуки-заглушки) |
| Коннекторы интеграций | echo, http, sandbox-fs, system | `integration/connectors.py` | Средняя | 3 |
| REST API + health/status | FastAPI эндпоинты | `api/app.py` | Высокая | 3 (нет auth) |

## Скрытые / незавершённые возможности (Phase 3)
- **STT/TTS — заглушки**: `_transcribe`/`_synthesize` возвращают переданный транскрипт или `None`
  (`multimodal/processor.py:52-62`). Голос реально не обрабатывается.
- **Postgres+pgvector / Neo4j / Redis** объявлены в extras (`pyproject.toml:27-29`) и в конфиге
  (`config.py:69-73`), но **нигде не используются** — память всегда SQLite, граф всегда in-process.
- **Описание изображений** — только текстовая подпись из вложения (`processor.py:31-34`), без vision-модели.
- **Профили роутинга** `local`/`fast` определены (`router.py:31-38`), но Ollama проверяется сетью при
  каждом вызове (`cloud.py:123-130`).
- **`acta-telegram`** — отдельный CLI-энтрипоинт, не упомянут в README-флоу.
- **`spawn`** запускает отделённые процессы без отслеживания PID после ответа (`system.py:87-99`).
