# Arquitetura do Personal Assistant

## Visão Geral

O Personal Assistant segue uma arquitetura de **backend compartilhado com frontends separados**. O motor de IA (runtime, tools, memória) é idêntico para Telegram e Web — apenas a camada de interface e autenticação difere.

```
┌─────────────────┐     ┌─────────────────────┐
│  Telegram Bot    │     │  Web App (FastAPI)   │
│  run.py          │     │  run_web.py          │
│  telegram_bot.py │     │  web_app/app.py      │
│                  │     │  web_app/auth.py     │
│  Auth: User ID   │     │  Auth: JWT + bcrypt  │
│  allowlist       │     │  web_app/user_store  │
└────────┬────────┘     └──────────┬──────────┘
         │                         │
         │   ┌─────────────────┐   │
         └──►│  Core Engine    │◄──┘
             │                 │
             │  AssistantService        (entry point)
             │  AssistantRuntime        (orchestration)
             │  ToolRegistry            (dynamic handlers)
             │  ConversationMemoryStore (SQLite)
             │  UserCredentialStore     (encrypted)
             │  FileStore               (uploads)
             └────────┬────────┘
                      │
         ┌────────────┼────────────┐
         ▼            ▼            ▼
   ┌──────────┐ ┌──────────┐ ┌──────────┐
   │  OpenAI  │ │  Notion  │ │Gmail/Cal │
   │Connector │ │Connector │ │Connector │
   └──────────┘ └──────────┘ └──────────┘
```

---

## Entry Points

| Entry Point | Comando | Processo |
|---|---|---|
| **Telegram** | `python run.py` | `python-telegram-bot` polling loop + scheduler daemon |
| **Web** | `python run_web.py` | `uvicorn` ASGI server (FastAPI) na porta 8001 |

Cada processo cria sua própria instância de `AssistantService` (singleton lazy), mas ambos apontam para o **mesmo banco SQLite** e **mesma config** (`agents.json`).

---

## Isolamento de Sessão

A separação entre usuários e entre canais é feita por **Session ID**, não por bancos separados.

```
Telegram:  dm:{chat_id}:{telegram_user_id}
           ex: dm:987654321:123456789

Web:       dm:web:{username}:{conversation_id}:web:{username}
           ex: dm:web:carlos:conv-abc123:web:carlos
```

Cada conversa tem seu próprio histórico de mensagens e tool calls no SQLite, isolados pelo `session_id`.

---

## Core Engine — Fluxo de Mensagem

```
Mensagem do Usuário
    │
    ▼
AssistantService.chat(user_id, channel_id, message)
    │
    ├── Build session_id
    ├── memory_store.append_message(session_id, "user", message)
    ├── Resolve user memories (arquivos .md do diretório do usuário)
    ├── memory_store.get_recent_messages(session_id, limit=20)
    │
    ▼
AssistantRuntime.process_user_message()
    │
    ├── Monta contexto: system prompt + user memories + histórico
    ├── OpenAI API call (model: gpt-4.1-mini)
    │
    ├── Se LLM retorna tool_calls:
    │   ├── ToolRegistry.execute_tool(name, args, context)
    │   │   └── importlib → handler function → resultado
    │   ├── memory_store.log_tool_call(session_id, ...)
    │   └── Nova chamada OpenAI com resultado da tool
    │   (repete até max_tool_rounds=6)
    │
    ├── memory_store.append_message(session_id, "assistant", response)
    │
    ▼
ChatResponse(text, image_paths)
    │
    ├── Telegram: split em chunks de 4096 chars, Markdown → HTML
    └── Web: JSON response com markdown + image URLs
```

---

## Sistema de Tools

### Definição → Registro → Execução

```
agents.json                          (30+ tools definidas)
    ↓ config_loader.py
AssistantConfiguration {agents, tools}
    ↓
ToolRegistry._tool_definitions       (mapa nome → definição)
    ↓ get_openai_tools()
OpenAI function definitions          (enviadas ao LLM)
    ↓ LLM decide chamar uma tool
Tool call: {name, arguments}
    ↓ runtime._execute_tool_call()
ToolRegistry.execute_tool()
    ↓ _resolve_handler("module.path:function_name")
importlib.import_module → handler(arguments, context)
    ↓
Resultado (dict) → devolvido ao LLM
```

### Categorias de Tools

| Módulo | Tools | Descrição |
|---|---|---|
| `notion_tools.py` | tasks, notes, expenses, meals, exercises, bills | CRUD no Notion |
| `email_tools.py` | send, search, read, attachments | Gmail integration |
| `calendar_tools.py` | list/create events (+ Meet links) | Google Calendar |
| `memory_tools.py` | list/read/edit memory files | Memória persistente |
| `file_tools.py` | list/read/delete uploads | Arquivos do usuário |
| `scheduled_task_tools.py` | CRUD scheduled tasks | Agendamento |
| `contacts_tools.py` | search/register contacts | CSV com fuzzy search |
| `chart_tools.py` | nutrition/exercise charts | Matplotlib |
| `news_tools.py` | Google News + Hacker News | Agregação de notícias |
| `meta_tools.py` | list tools/agents | Introspecção |
| `dev_tools.py` | run_copilot_task, restart_bot | DevOps via Telegram |

---

## Resolução de Credenciais

Todas as integrações seguem o mesmo padrão de resolução:

```
1. UserCredentialStore.get_credential(user_id, key)
   └── Busca credencial criptografada (Fernet) no SQLite
       Tabela: user_credentials (user_id, credential_key, credential_value)

2. Se não encontrou → os.getenv(ENV_VAR)
   └── Fallback para variável de ambiente

3. Se não existe → erro ou integração desabilitada
```

**Exemplos de credenciais per-user:**
- `notion_api_key`, `notion_database_id` → Notion
- `google_token_json` → Gmail + Calendar (token OAuth2 completo)
- `email_from`, `email_to`, `display_name` → Config de email

Isso permite que cada usuário (Telegram ou Web) tenha suas próprias integrações configuradas.

---

## Memórias de Usuário (Arquivos)

### Estrutura no disco

```
memories/
├── README.md                 (ignorado pela engine)
├── 6496576962/               (Telegram user ID)
│   ├── about-me.md
│   ├── health.md
│   ├── personal-assistant.md
│   └── contacts.csv
└── webcarlos/                (web:carlos → sanitizado)
    ├── about-me.md
    ├── health.md
    ├── personal-assistant.md
    └── contacts.csv
```

### Fluxo de resolução

```python
# runtime._resolve_user_memories_dir(user_id)
user_id = "web:carlos"
safe_id = re.sub(r"[^a-zA-Z0-9_\-]", "", user_id)  # → "webcarlos"
user_dir = memories_dir / safe_id                     # → memories/webcarlos/
# Validação: realpath deve ficar dentro de memories_dir (anti path-traversal)
```

O conteúdo dos arquivos `.md` é injetado no contexto do LLM como memória persistente, com seleção por relevância (synonym matching).

---

## Banco de Dados (SQLite)

Um único arquivo `assistant_memory.sqlite3` (modo WAL) contém **todas** as tabelas:

### Tabelas Core (assistant_connector)

| Tabela | Uso |
|---|---|
| `conversation_messages` | Histórico de chat (session_id, role, content) |
| `tool_calls` | Log de tool calls (session_id, tool_name, args, result) |
| `scheduled_tasks` | Tarefas agendadas (cron-like recurrence + retry) |
| `memory_audit_log` | Auditoria de edições em arquivos de memória |
| `user_files` | Metadata de uploads (file_id, user_id, original_name) |
| `user_credentials` | Credenciais criptografadas per-user (Fernet) |

### Tabelas Web (web_app/user_store)

| Tabela | Uso |
|---|---|
| `web_users` | Contas web (username UNIQUE NOCASE, bcrypt hash) |
| `web_conversations` | Metadata de conversas (user_id, title, timestamps) |
| `web_notes` | Notas do usuário (title, content, max 500KB) |
| `web_note_tags` | Tags de notas (note_id, tag) com CASCADE |

Thread safety: todas as operações SQLite usam `threading.Lock()`.

---

## Componentes Compartilhados vs Separados

| Componente | Compartilhado | Detalhes |
|---|---|---|
| SQLite Database | ✅ | Arquivo único para Telegram + Web |
| ConversationMemoryStore | ✅ | Mesmo DB, isolamento por session_id |
| AssistantRuntime | ✅ | Mesmo código, instância separada por processo |
| ToolRegistry + agents.json | ✅ | Mesma config, mesmas tools disponíveis |
| UserCredentialStore | ✅ | Mesma tabela, isolamento por user_id |
| FileStore | ✅ | Mesmo diretório e tabela de uploads |
| Memory Files (memories/) | ✅ | Mesmo diretório base, subpastas por user_id |
| OpenAI Connector | ✅ | Mesmo client, mesmo model |
| Notion/Gmail/Calendar | ✅* | Mesmo código, criados per-request com credenciais do user |
| Autenticação | ❌ | Telegram: ID allowlist / Web: JWT + bcrypt |
| Session IDs | ❌ | Formato diferente (garante isolamento) |
| User Management | ❌ | Telegram: sem cadastro / Web: web_users table |
| Conversations UI | ❌ | Web-only (CRUD + auto-prune max 100) |
| Notes | ❌ | Web-only (CRUD + tags + LLM metadata) |
| Google OAuth Flow | ❌ | Web-only (callback → credential_store) |
| Scheduler (cron) | ❌ | Telegram-only (daemon thread) |

---

## Web App — Camadas Específicas

### Autenticação (JWT)

```
POST /api/auth/login → {username, password}
    → bcrypt.verify(password, stored_hash)
    → jwt.encode({sub: user_id, username, exp: 72h}, WEB_JWT_SECRET, HS256)
    → {token: "eyJ..."}

GET /api/* (protegido)
    → Header: Authorization: Bearer <token>
    → verify_token() → {user_id, username}
```

### Google OAuth (Web)

```
GET /api/google/auth-url
    → Gera URL de autorização Google (Gmail + Calendar scopes)
    → Redirect para Google

GET /api/google/callback?code=...
    → Troca code por access_token + refresh_token
    → credential_store.set_credential(user_id, "google_token_json", token)
    → Redirect para /

DELETE /api/google/disconnect
    → credential_store.delete_credential(user_id, "google_token_json")
```

### Endpoints da Web App

| Grupo | Rotas | Descrição |
|---|---|---|
| Auth | `POST /login`, `GET /me` | Login JWT + validação |
| Chat | `POST /chat`, `/upload`, `/audio`, `/reset` | Mensagem, arquivo, áudio, reset |
| Conversas | `GET/POST/PATCH/DELETE /conversations` | CRUD + auto-prune (max 100/user) |
| Notas | `GET/POST/PATCH/DELETE /notes` | CRUD + tag filter (`?tag=`) |
| Tags | `GET /notes/tags`, `POST /notes/{id}/generate-metadata` | Tags únicas + geração LLM |
| Notion | `GET /notion/check` | Status de conexão por DB |
| Memórias | `GET /memories` | Lista arquivos .md do usuário |
| Google | `GET /google/status`, `/auth-url`, `/callback`, `DELETE /disconnect` | OAuth2 flow |
| Health | `GET /health` | `{status: "ok"}` |

---

## Scheduler (Telegram-only)

```
telegram_bot.py
    └── AssistantScheduledTaskRunner (daemon thread)
        └── Poll a cada 5s: memory_store.claim_next_scheduled_task()
            └── Se encontra task com scheduled_for <= now:
                ├── service.execute_next_scheduled_task()
                ├── Retry com backoff exponencial (30s → 900s)
                ├── Recurrence: "daily"/"weekly"/"monthly" → re-schedule
                └── Resultado enviado ao usuário via Telegram bot
```

Apenas o processo do Telegram roda o scheduler. Tasks criadas via Web são executadas quando o bot está ativo.

---

## Conectores Externos

Todos seguem o mesmo padrão: **criados per-request, credenciais resolvidas per-user**.

```python
# Padrão comum:
def connector_connect(project_logger, user_id=None, credential_store=None):
    # 1. Tenta per-user credential
    token = credential_store.get_credential(user_id, "key")
    # 2. Fallback para env var / token.json
    # 3. Retorna client autenticado ou erro
```

| Conector | Credencial | Fallback |
|---|---|---|
| **Notion** | `notion_api_key` + `notion_database_id` | `NOTION_API_KEY` + `NOTION_DATABASE_ID` |
| **Gmail** | `google_token_json` | `token.json` (arquivo local) |
| **Calendar** | `google_token_json` | `token.json` (arquivo local) |
| **OpenAI** | — | `OPENAI_KEY` (sempre env) |

---

## Deploy

```
Servidor: Ubuntu 22.04 + Nginx + systemd

┌─────────────────────────────────────────────┐
│                   Nginx                      │
│  app.carlosplf.com → localhost:8001 (Web)   │
│  SSL via Let's Encrypt                       │
└───────────────────┬─────────────────────────┘
                    │
    ┌───────────────┼───────────────┐
    ▼                               ▼
personal-assistant-web.service   personal-assistant-bot.service
    uvicorn (port 8001)           python run.py (polling)
    web_app.app:app               telegram_bot
```

Deploy: `sudo ./deploy/deploy_web_app.sh` (idempotente).
