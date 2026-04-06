# AGENTS.md — Personal Assistant

Este arquivo complementa `/home/carlos/AGENTS.md` com regras específicas deste projeto.

## Objetivo do projeto

Assistente pessoal multi-canal (Telegram + Web PWA) que integra Notion, OpenAI, Gmail e Google Calendar para:
- Gerenciar tarefas, notas, despesas, refeições e exercícios (Notion).
- Chat com IA usando tool-calling (OpenAI).
- Enviar/buscar emails (Gmail) e gerenciar agenda (Calendar).
- Interface web PWA com conversas, anotações com tags, e integrações Google OAuth.

## Estrutura do projeto

```
personal-assistant/
├── run.py                    # Entry point: Telegram bot
├── run_web.py                # Entry point: Web app PWA (uvicorn)
├── telegram_bot.py           # Telegram bot: auth, message handling, chunking
├── task_summary_flow.py      # Fluxo Notion → LLM → resumo de tarefas
├── google_auth_server.py     # OAuth callback server (Telegram)
│
├── web_app/                  # === WEB APP (FastAPI + vanilla JS) ===
│   ├── app.py                # FastAPI routes (~618 linhas): auth, chat, conversations, notes, Notion, Google OAuth
│   ├── auth.py               # JWT: create_access_token(), verify_token() — HS256, 72h default
│   ├── dependencies.py       # Singletons: user_store, assistant_service, google_oauth, credential_store, get_current_user()
│   ├── user_store.py         # SQLite CRUD (~441 linhas): web_users, web_conversations, web_notes, web_note_tags
│   ├── google_oauth.py       # Google OAuth2 flow (Gmail + Calendar scopes)
│   ├── manage_users.py       # CLI: python -m web_app.manage_users create|list|deactivate|change-password
│   ├── templates/
│   │   └── chat.html         # Single-page HTML template (PWA)
│   └── static/
│       ├── js/app.js         # Frontend IIFE (~1200 linhas): chat, notes, sidebar, search, audio, Google OAuth
│       ├── js/audio-recorder.js  # AudioRecorder: MediaRecorder wrapper
│       └── css/style.css     # Design system: CSS custom properties, responsive, sidebar collapse
│
├── assistant_connector/      # === CORE AI FRAMEWORK ===
│   ├── service.py            # AssistantService: chat(), reset_chat(), handle_file_upload()
│   ├── runtime.py            # AssistantRuntime: tool execution orchestration, conversation memory, response formatting
│   ├── models.py             # ChatResponse, ToolDefinition, AgentDefinition, ToolExecutionContext
│   ├── config_loader.py      # Parses agents.json → tools + agents
│   ├── tool_registry.py      # Dynamic handler loading via "module.path:function_name"
│   ├── memory_store.py       # ConversationMemoryStore: SQLite message/tool-call/audit history
│   ├── file_store.py         # FileStore: upload/storage (.pdf, .txt, .csv, .md, .docx, max 20MB)
│   ├── user_credential_store.py  # Encrypted per-user credential storage (Fernet)
│   ├── scheduler.py          # Background task scheduler (cron-like recurrence + retry)
│   ├── app_health.py         # Health check utilities
│   ├── charts/               # Matplotlib chart generation
│   ├── config/
│   │   └── agents.json       # Tool + agent definitions (~1600 linhas, 30+ tools, multiple agents)
│   └── tools/                # === TOOL PLUGINS ===
│       ├── notion_tools.py       # tasks, notes, expenses, meals, exercises, monthly bills
│       ├── calendar_tools.py     # list_calendar_events, create_calendar_event (with Meet links)
│       ├── email_tools.py        # send, search, read, attachment analysis (PDF/DOCX/XLSX)
│       ├── memory_tools.py       # list/read/edit user memory files (with audit log)
│       ├── file_tools.py         # list/read/delete uploaded files
│       ├── scheduled_task_tools.py  # CRUD for persistent scheduled tasks
│       ├── contacts_tools.py     # CSV contacts: fuzzy search, register, resolve email
│       ├── metabolism_tools.py   # BMR/calorie calculator (Harris-Benedict)
│       ├── chart_tools.py        # Nutrition/exercise chart generation
│       ├── news_tools.py         # Google News + Hacker News aggregation
│       ├── system_tools.py       # Hardware status: memory, uptime
│       ├── user_credential_tools.py  # manage_user_credentials (get/set/delete encrypted)
│       ├── meta_tools.py         # list_available_tools, list_available_agents
│       └── dev_tools.py          # run_copilot_task, restart_bot_service (from Telegram)
│
├── notion_connector/         # Notion API integration (~2600 linhas)
│   └── notion_connector.py   # Multi-database: tasks, notes, expenses, meals, exercises, bills
│
├── openai_connector/         # OpenAI API integration (~690 linhas)
│   └── llm_api.py            # LLM calls, prompt templates, audio transcription, note metadata generation
│
├── gmail_connector/          # Gmail API integration (~640 linhas)
│   └── gmail_connector.py    # Email send/search/read, attachment extraction, HTML templates
│
├── calendar_connector/       # Google Calendar API
│   └── calendar_connector.py # Event list/create with Meet links, timezone handling
│
├── utils/                    # Utilities
│   ├── create_logger.py      # Rotating file logger (5MB/file, 3 backups)
│   ├── load_credentials.py   # Credential resolution: per-user store → env fallback
│   ├── message_parser.py     # JSON extraction from LLM output
│   ├── timezone_utils.py     # IANA + UTC offset timezone management
│   └── nice_message_collector.py  # Motivational quote API
│
├── templates/
│   └── email_template.html   # Jinja2 email template (task summary, GPT analysis, quotes)
│
├── memories/                 # User memory files (.txt, .md, .json) + contacts.csv
├── news-sources/             # RSS/news source config
├── banner/                   # Project logo/banner
│
├── tests/                    # Pytest test suite (~630+ tests)
├── pytest.ini                # Coverage config: 80% threshold
├── requirements.txt          # Python dependencies
│
└── deploy/                   # Production deployment
    ├── deploy_web_app.sh     # Idempotent deploy script (Ubuntu 22.04+)
    ├── nginx_web_app.conf    # Nginx reverse proxy (port 8001, SSL/Let's Encrypt)
    ├── nginx_google_oauth.conf  # OAuth callback routing
    └── systemd/
        ├── personal-assistant-web.service  # uvicorn web app
        └── personal-assistant-bot.service  # Telegram bot
```

## Web App — Arquitetura detalhada

### Backend (FastAPI)

**Endpoints principais:**

| Grupo | Rotas | Descrição |
|-------|-------|-----------|
| Auth | `POST /api/auth/login`, `GET /api/auth/me` | Login JWT + validação |
| Chat | `POST /api/chat`, `/chat/upload`, `/chat/audio`, `/chat/reset` | Mensagem, arquivo, áudio, reset |
| Conversas | `GET/POST/PATCH/DELETE /api/conversations` | CRUD + auto-prune (max 100/user) |
| Notas | `GET/POST/PATCH/DELETE /api/notes` | CRUD + tag filter (`?tag=`) |
| Tags | `GET /api/notes/tags`, `POST /api/notes/{id}/generate-metadata` | Tags únicas + geração LLM |
| Notion | `GET /api/notion/check` | Status de conexão por DB |
| Google | `GET /api/google/status`, `/auth-url`, `/callback`, `DELETE /disconnect` | OAuth2 flow |
| Health | `GET /api/health` | `{status: "ok"}` |

**Patterns:**
- Session ID: `web:{username}` ou `web:{username}:{conversation_id}`
- Message limit: 40 mensagens/conversa (20 trocas)
- Async threading: `asyncio.to_thread()` para operações pesadas
- Dependency injection: `Depends()` para auth, stores, services

### Database (SQLite)

Arquivo: `assistant_memory.sqlite3` (WAL mode, foreign keys ON)

**Tabelas web:**
- `web_users` — id, username (UNIQUE NOCASE), password_hash (bcrypt), display_name, is_active
- `web_conversations` — id, user_id, title, created_at, updated_at (INDEX user+updated DESC)
- `web_notes` — id, user_id, title, content (max 500KB), created_at, updated_at
- `web_note_tags` — (note_id, tag) PK, FK CASCADE, INDEX on tag

**Tabelas core (assistant_connector):**
- Conversation messages, tool call history, audit log, scheduled tasks, file metadata, user credentials (encrypted)

### Frontend (Vanilla JS)

**Arquitetura:** Single IIFE (`app.js`, ~1200 linhas), sem framework.

**Estado principal:**
- `token` — JWT em localStorage (`pa_token`)
- `activeConversationId` — Conversa ativa (localStorage: `pa_active_conversation`)
- `activeTab` — `"chat"` ou `"notes"`
- `activeNoteId` — Nota sendo editada
- `activeNoteContentDirty` — Flag de conteúdo não commitado para metadata
- `easyMDE` — Editor Markdown (CodeMirror)
- `allUserTags` / `activeTagFilter` — Tags para autocomplete e filtro

**Fluxos UI:**

1. **Chat:** textarea → POST /api/chat → render markdown (marked.js) → scroll
2. **Upload:** file picker → FormData POST /api/chat/upload → resposta assistente
3. **Áudio:** AudioRecorder → POST /api/chat/audio → transcrição + resposta
4. **Notas — edição:** seleciona nota → EasyMDE → auto-save (debounce 2s) → metadata LLM (debounce 5s)
5. **Notas — busca:** toggle lupa → input → autocomplete tags → filtro API `?tag=`
6. **Notas — metadata LLM:** POST /api/notes/{id}/generate-metadata → atualiza título + tag pills na sidebar e editor
7. **Tab switch:** sliding pill animation (CSS ::before + translateX) com largura fixa
8. **Sidebar collapse:** desktop only, estado em localStorage (`pa_sidebar_collapsed`)
9. **Empty states:** "Selecione uma Conversa/Nota" + botão de criar quando área principal está vazia

**Libs externas (CDN):**
- `marked.js` — Renderização Markdown
- `EasyMDE` — Editor Markdown com toolbar

**Design tokens (CSS custom properties):**
```css
--color-bg: #FFFFFF;
--color-text: #1A1A1A;
--color-text-muted: #6B7280;
--color-border: #E5E7EB;
--color-blue-start: #1663DE;
--color-blue-end: #80C4E8;
--header-height: 56px;
--sidebar-width: 280px;
--radius-sm: 8px; --radius-md: 12px; --radius-lg: 16px;
```

## OpenAI — Patterns

- **Model padrão:** `gpt-4.1-mini` (variável `DEFAULT_LLM_MODEL`)
- **API style:** `openai.OpenAI` client → `responses.create()` (não `chat.completions`)
- **Error wrapper:** `_safe_openai_call()` → `OpenAICallError` com mensagens em português
- **Note metadata:** `generate_note_metadata(content, logger)` → `{"title": str, "tags": list}` — trunca em 4000 chars, max 5 tags lowercase
- **Audio:** `gpt-4o-mini-transcribe` via `transcribe_audio_input()`
- **Prompts:** todos em português, definidos como constantes no módulo

## Notion — Patterns

- **Session:** `requests.Session` com retry (exponential backoff)
- **API versions:** tenta v2025-09-03 e v2022-06-28 para compatibilidade
- **Multi-DB:** tasks, notes, expenses, meals, exercises, monthly bills
- **Credentials:** per-user store → env fallback (`NOTION_API_KEY`, `NOTION_DATABASE_ID`, etc.)
- **Rich text:** parsing de markdown → Notion block children, chunks de 1800 chars

## Convenções de implementação

1. Separação de responsabilidades por conector (Notion/OpenAI/Gmail/Calendar).
2. Evite lógica de integração diretamente em `run.py` ou `app.py`; prefira funções nos módulos conector/utilitários.
3. Nunca hardcode credenciais; sempre use variáveis de ambiente (`.env`) ou `UserCredentialStore`.
4. Preserve logs úteis para diagnóstico usando o logger do projeto (`utils/create_logger.py`).
5. Comentários em código devem ser em inglês.
6. UI text em português brasileiro.
7. Thread safety: use `threading.Lock()` para operações SQLite.
8. Credential resolution: per-user store primeiro → env fallback.
9. Web app: use `dependency_overrides` para mock em testes; restaure `httpx.Client.request` para TestClient funcionar.
10. Testes: use `monkeypatch.setenv(var, "")` em vez de `monkeypatch.delenv()` para prevenir reload do `.env`.

## Execução local

```bash
python3 -m venv ./env
source ./env/bin/activate
pip install -r requirements.txt

# Telegram bot
python run.py

# Web app PWA (default: 0.0.0.0:8001)
python run_web.py
```

## Testes

```bash
./env/bin/python -m pytest -q
```

- **~630+ testes**, threshold de cobertura: **80%**
- Coverage: `assistant_connector`, `gmail_connector`, `openai_connector`, `calendar_connector`, `google_auth_server`, `telegram_bot`, `utils`
- Web app tests: `test_web_app.py`, `test_web_auth.py`, `test_web_conversations.py`, `test_web_notes.py`, `test_web_google_oauth.py`, `test_web_notion_status.py`, `test_web_user_store.py`
- Conftest: bloqueia rede real; testes web restauram `httpx.Client.request`

## Gerenciamento de usuários web

```bash
python -m web_app.manage_users create --username carlos --password <senha> [--display-name "Carlos"]
python -m web_app.manage_users list
python -m web_app.manage_users deactivate --username carlos
python -m web_app.manage_users change-password --username carlos --password <nova_senha>
```

## Configuração esperada (.env)

**Core:**
- `NOTION_DATABASE_ID`, `NOTION_API_KEY` — Notion integration
- `OPENAI_KEY` — OpenAI API key
- `EMAIL_FROM`, `EMAIL_TO`, `DISPLAY_NAME` — Gmail config
- `LOG_PATH` — Log directory (default: current dir)
- `TIMEZONE` — IANA timezone (default: `America/Sao_Paulo`)

**Web app:**
- `WEB_JWT_SECRET` — **Obrigatório** para JWT
- `WEB_JWT_EXPIRY_HOURS` — Expiração token (default: 72)
- `WEB_HOST` — Bind address (default: `0.0.0.0`)
- `WEB_PORT` — Porta (default: `8001`)
- `WEB_RELOAD` — Hot reload (default: `0`)
- `GOOGLE_OAUTH_CALLBACK_URL` — Habilita OAuth (se não definido, OAuth desabilitado)

**Segurança:**
- `CREDENTIAL_ENCRYPTION_KEY` — Fernet key para credenciais per-user
- `TELEGRAM_ALLOWED_USER_IDS` — Allowlist de user IDs (obrigatório no boot do bot)

**Arquivos locais (não versionar):**
- `credentials.json` — Google Cloud OAuth client
- `token.json` — gerado após autenticação Gmail (sistema)

## Deploy (produção)

- **Domínio:** `app.carlosplf.com`
- **Stack:** Ubuntu 22.04 + Nginx (SSL/Let's Encrypt) + systemd + uvicorn
- **Deploy:** `sudo ./deploy/deploy_web_app.sh` (idempotente)
- **Services:** `personal-assistant-web.service` (PWA), `personal-assistant-bot.service` (Telegram)

## Validação de mudanças

1. Rode `./env/bin/python -m pytest -q` e confirme que todos os testes passam (80%+ coverage).
2. Se a mudança não exigir APIs externas, prefira validação local isolada.
3. Se alterar fluxo de envio, use modo de teste para evitar disparo real de emails.
4. Ao adicionar comportamento novo, inclua testes em `tests/` quando viável.
5. Mudanças no frontend: teste em mobile (viewport ≤ 768px) e desktop.
