# ACTA GHOST — Autonomous Cognitive Task Assistant

<p align="center">
  <img src="acta/web/logo.png" alt="ACTA GHOST" width="160" />
</p>

ACTA GHOST is a personal **cognitive operating environment**: an agentic, multi-model,
memory-driven platform that understands context, reasons, plans, and autonomously
executes multi-step tasks on the user's behalf.

This repository contains a fully runnable **MVP** of the ACTA core described in
[`OVERVIEW.md`](OVERVIEW.md) and [`ARCHITECTURE.md`](ARCHITECTURE.md). It runs
**100% offline** with zero credentials (a deterministic mock model provider) and
upgrades transparently to OpenAI / Anthropic / Gemini / Ollama when configured.

---

## Highlights

- **12 specialized sub-agents** orchestrated through a single cognitive pipeline.
- **Full OS control** — run/stop programs, manage processes & services, and
  create/edit/delete files anywhere on the host (audited, toggleable).
- **Trilingual** — Russian / Hebrew / English with automatic language detection
  and an RTL-aware UI; ACTA replies in the user's language.
- **WhatsApp & Telegram** — chat with ACTA from messengers (Telegram polling or
  webhook; WhatsApp via Meta Cloud API).
- **Unified Provider Layer + AI Router** — local & cloud models behind one
  interface, with rule-based routing and automatic fallback.
- **Memory System** — episodic, semantic, personal & procedural memory on an
  **encrypted** SQLite store with lexical (TF-IDF-lite) retrieval.
- **Knowledge Graph** — entities & relations (networkx) with search and path
  analysis, persisted to disk.
- **Security Layer** — Fernet encryption at rest, capability-based agent
  permissions, append-only audit log.
- **Integration Layer** — pluggable connector framework (echo / HTTP / sandboxed
  filesystem) ready for REST, GraphQL, Webhooks and MCP.
- **Multimodal Layer** — text in/out today; clean hooks for Whisper STT and Piper TTS.
- **Web UI** — a modern chat interface with a live agent panel and an execution
  inspector (trace / plan / intent).

## The twelve sub-agents

| # | Agent | Responsibility |
|---|-------|----------------|
| 1 | Intent | Determine the user's intent from the input |
| 2 | Context | Build & update the dynamic user model |
| 3 | Reasoning | Construct logical chains toward the goal |
| 4 | Planning | Decompose the goal into an executable plan |
| 5 | Decision | Choose a strategy & route tasks to agents/models |
| 6 | Orchestrator | Manage agent lifecycle, progress & result integration |
| 7 | Memory | Retrieve & persist all four memory types |
| 8 | Knowledge Graph | Search & grow the relation graph |
| 9 | Integration | Talk to external services & local resources |
| 10 | Security | Enforce permissions, encryption & audit |
| 11 | Multimodal | Normalize inputs & render outputs across modalities |
| 12 | UI | Present consolidated results to the user |

Each agent carries its exact specification sub-prompt in `agent.SUB_PROMPT`.
Worker agents (`research`, `coding`, `automation`, `system`) execute plan tasks.

## Full OS control

The `system` worker + `SystemConnector` give ACTA real control of the host:

| Action | Description |
|--------|-------------|
| `exec` / `spawn` | Run a shell command (captured) / launch a program detached |
| `processes` / `kill` | List processes / terminate by pid or name |
| `service` | start / stop / restart / status (launchctl / systemctl / sc) |
| `fs` | create / read / write / append / delete / move / mkdir / list — anywhere |
| `info` | OS, CPU, memory, user, cwd |

Issue it in natural language ("покажи информацию о системе", "list processes")
or with a precise directive in request metadata:

```bash
curl -s http://127.0.0.1:8765/api/chat -H 'Content-Type: application/json' -d '{
  "text": "создай файл",
  "metadata": {"system": {"action": "fs",
    "params": {"op": "write", "path": "/tmp/note.txt", "content": "hello"}}}
}'
```

> ⚠️ This is real, unrestricted control of your machine. It is gated by
> `ACTA_ALLOW_SYSTEM_CONTROL` (default `true`), the `system.control` capability,
> and every action is written to the audit log. Set the flag to `false` to disable.

## Languages (RUS / HEB / ENG)

ACTA detects the input language by script and responds in it; the web UI has a
RUS / עברית / ENG switcher with full RTL layout for Hebrew. Force a language with
`metadata.language` (`ru` | `he` | `en`) or the `ACTA_DEFAULT_LANGUAGE` fallback.

## Messaging channels

| Channel | Setup |
|---------|-------|
| **Telegram** | Set `ACTA_TELEGRAM_BOT_TOKEN`. ACTA long-polls on startup, or run `acta-telegram` standalone. Webhook mode: set `ACTA_TELEGRAM_WEBHOOK_URL` → `/webhooks/telegram`. |
| **WhatsApp** | Set `ACTA_WHATSAPP_TOKEN`, `ACTA_WHATSAPP_PHONE_ID`, `ACTA_WHATSAPP_VERIFY_TOKEN`. Point the Meta webhook to `/webhooks/whatsapp`. |

Each messenger user gets isolated memory (`telegram:<id>` / `whatsapp:<phone>`).

## Pipeline flow

```
UI intake → Multimodal(normalize) → Intent → Memory(retrieve) → Knowledge Graph
→ Context → Reasoning → Planning → Decision → Orchestrated execution (workers)
→ Integration → Security → UI(compose) → Memory(persist) → Multimodal(render)
```

Worker tasks run **sequentially or in parallel** per the Decision Agent's
strategy, always respecting task dependencies.

---

## Quickstart

Requires Python 3.11+. [`uv`](https://github.com/astral-sh/uv) is recommended.

```bash
# 1. Create the environment and install (offline-ready, no keys needed)
uv venv --python 3.11
uv pip install -e ".[dev]"

# 2. Run the test suite
uv run pytest -q

# 3. Start the server + web UI
uv run acta
# open http://127.0.0.1:8765
```

With plain `pip`:

```bash
python3.11 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest -q
acta
```

### Talk to ACTA from the CLI / HTTP

```bash
curl -s http://127.0.0.1:8765/api/chat \
  -H 'Content-Type: application/json' \
  -d '{"text":"Исследуй кэширование в Postgres и набросай план внедрения"}' | python -m json.tool
```

---

## Configuration

All settings are environment variables prefixed with `ACTA_` (see
[`.env.example`](.env.example)). Everything has an offline default.

Enable a real model provider, e.g. OpenAI:

```bash
export ACTA_DEFAULT_PROVIDER=openai
export ACTA_OPENAI_API_KEY=sk-...
pip install -e ".[openai]"
```

The **AI Router** automatically chooses providers per task profile (reasoning,
planning, coding, fast, local) and falls back to the offline mock if a provider
is unavailable, so ACTA never breaks.

### What changes when a real model is connected

ACTA detects a configured provider (`router.real_available()`) and upgrades two
things automatically:

- **Full-quality answers in any language.** The UI Agent synthesizes the final,
  self-contained reply with the model, in the user's detected language
  (RUS/HEB/ENG), integrating all worker results — instead of the offline
  deterministic composition.
- **Natural-language OS control.** The System Agent asks the model to translate
  free-form instructions into a strict JSON action for the target OS, e.g.
  *"открой калькулятор"* → `{"action":"spawn","params":{"command":"open -a Calculator"}}`.
  Explicit `metadata.system` directives and the built-in NL parser remain as
  fast, offline-safe paths.

Local example with Ollama (no API key, fully private):

```bash
ollama serve & ollama pull llama3.1
export ACTA_DEFAULT_PROVIDER=ollama
uv run acta
```

### Optional production backends

The MVP needs none of these, but the interfaces are ready for them:

| Concern | MVP | Production (optional extras) |
|---------|-----|------------------------------|
| Memory | encrypted SQLite | PostgreSQL + pgvector (`.[postgres]`) |
| Knowledge Graph | networkx + JSON | Neo4j (`.[neo4j]`) |
| Queue | in-process | Redis Streams / NATS (`.[redis]`) |
| Models | mock | OpenAI / Anthropic / Gemini / Ollama |

---

## API

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/chat` | Run the full pipeline on a message |
| `GET` | `/api/status` | Providers, memory & graph stats, connectors |
| `GET` | `/api/agents` | List agents with sub-prompts & capabilities |
| `GET` | `/api/memory` | Recent memory records |
| `GET` | `/api/audit` | Tail of the audit log |
| `GET` | `/api/channels` | Telegram / WhatsApp status |
| `GET` | `/api/health` | Health check |
| `POST` | `/webhooks/telegram` | Telegram webhook (webhook mode) |
| `GET/POST` | `/webhooks/whatsapp` | WhatsApp verify / inbound messages |

## Project layout

```
acta/
  config.py            # settings (env-driven, offline defaults)
  schemas.py           # typed messages flowing through the pipeline
  i18n.py              # RUS/HEB/ENG detection + system phrases
  providers/           # Unified Provider Layer + AI Router
  security/            # crypto, audit, permissions
  memory/              # encrypted SQLite memory store (4 kinds)
  knowledge_graph/     # networkx graph + search
  integration/         # connector framework + SystemConnector (full OS control)
  multimodal/          # input normalization & output rendering
  agents/              # the 12 sub-agents + workers (research/coding/automation/system)
  orchestrator/        # pipeline state + orchestrator
  channels/            # Telegram & WhatsApp adapters + channel hub
  api/                 # FastAPI app (+ channel webhooks)
  web/                 # chat UI (HTML/CSS/JS, trilingual + RTL)
tests/                 # pytest suite
```

## Security model

- All user content is encrypted at rest with Fernet (AES-128-CBC + HMAC).
- Keys come from `ACTA_ENCRYPTION_KEY`, are derived from
  `ACTA_MASTER_PASSWORD`, or are generated and stored `0600` in the data dir.
- Agents operate under least-privilege capabilities; the Security Agent verifies
  encryption and permissions on every request and writes an audit trail.
- Data stays local; the system can run entirely offline.

## License

MIT
