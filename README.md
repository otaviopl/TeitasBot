# Personal Assistant :rocket:

A personal productivity web app (PWA) powered by LLM tool-calling. Manage tasks, notes, finances, health tracking, email, and calendar — all from a single conversational interface or dedicated UI screens.

All data is stored locally in SQLite. No external database dependencies.

## Features

### Conversational AI assistant
Chat with an AI assistant that can take actions on your behalf using 30+ integrated tools:
- **Tasks**: create, edit, and organize tasks by project with deadlines and tags.
- **Notes**: full Markdown notes with auto-generated titles and tags via LLM.
- **Expenses & bills**: register expenses by category, track monthly bills and payments.
- **Meals**: log meals with food, quantity (grams), and calorie estimation (LLM-inferred).
- **Exercises**: register and analyze workouts with calories burned.
- **Metabolism**: BMR/TDEE calculator with history tracking.
- **Google Calendar**: list upcoming events and create new ones (with Meet links).
- **Gmail**: send, search, and read emails; analyze attachments (PDF/DOCX/XLSX).
- **Scheduled tasks**: recurring or one-time reminders with retry logic.
- **News**: Google News + Hacker News aggregation by topic.
- **Contacts**: fuzzy search and registration from persistent memory.
- **Voice input**: audio messages transcribed and processed as text.
- **File uploads**: PDF, TXT, CSV, MD, DOCX (up to 20 MB).

### Dedicated UI screens
Beyond the chat, the web app provides direct interfaces for:
- **Tasks board** — Kanban-style grouped by deadline (overdue, today, this week, etc.) with inline creation.
- **Notes editor** — Markdown editor (EasyMDE) with tag management, search, and auto-save.
- **Health dashboard** — Meals, exercises, weekly summaries, and calorie goals.
- **Finance dashboard** — Expenses and recurring bills with payment tracking.

### PWA support
Installable as a Progressive Web App on mobile and desktop.

## Architecture

```
personal-assistant/
├── run_web.py                # Entry point: uvicorn web server
├── web_app/                  # FastAPI app, auth, routes, user store
│   ├── app.py                # API routes (auth, chat, conversations, notes, tasks, health, finance)
│   ├── auth.py               # JWT authentication (HS256)
│   ├── user_store.py         # SQLite CRUD for users, conversations, notes
│   ├── google_oauth.py       # Google OAuth2 flow
│   ├── manage_users.py       # CLI user management
│   ├── static/               # Frontend (vanilla JS, CSS)
│   └── templates/            # HTML template (single-page PWA)
│
├── assistant_connector/      # Core AI framework
│   ├── service.py            # AssistantService: chat, reset, file upload
│   ├── runtime.py            # Tool execution, conversation memory, response formatting
│   ├── config/agents.json    # Tool + agent definitions (30+ tools)
│   ├── tools/                # Tool plugins (tasks, email, calendar, memory, etc.)
│   ├── memory_store.py       # SQLite conversation + audit history
│   ├── file_store.py         # File upload/storage
│   └── user_credential_store.py  # Encrypted per-user credential storage (Fernet)
│
├── openai_connector/         # OpenAI API: LLM calls, audio transcription, metadata generation
├── gmail_connector/          # Gmail API: send/search/read, attachments, HTML templates
├── calendar_connector/       # Google Calendar API: events, Meet links
├── utils/                    # Logging, credentials, timezone, message parsing
├── memories/                 # Persistent user memory files + contacts
└── deploy/                   # Production: Nginx, systemd, deploy script
```

The assistant is **config-driven** — tools and agents are declared in `agents.json` and handlers are loaded dynamically via `module.path:function_name`.

## Setup

### 1) Python environment

```sh
python3 -m venv ./env
source ./env/bin/activate
pip install -r requirements.txt
```

### 2) Google credentials (Gmail + Calendar)

Follow the [Google API quickstart](https://developers.google.com/gmail/api/quickstart/python) to create OAuth credentials and place `credentials.json` in the project root.

Users authorize Google via the web app's Google OAuth flow (Settings → Connect Google).

### 3) Configure `.env`

Create a `.env` at the project root.

**Required:**

```env
# OpenAI
OPENAI_KEY="sk-..."

# Web app
WEB_JWT_SECRET="your-jwt-secret"                            # required — secret for signing JWT tokens
CREDENTIAL_ENCRYPTION_KEY="..."                             # required — generate: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

# Google OAuth (enables Gmail + Calendar integration)
GOOGLE_OAUTH_CALLBACK_URL="https://yourdomain.com/api/google/callback"
```

**Optional:**

```env
WEB_HOST="0.0.0.0"                                         # bind address (default: 0.0.0.0)
WEB_PORT="8001"                                             # port (default: 8001)
WEB_JWT_EXPIRY_HOURS="72"                                   # token expiry (default: 72)
WEB_RELOAD="0"                                              # hot reload for development (default: 0)
LLM_MODEL="gpt-4.1-mini"                                   # LLM model (default: gpt-4.1-mini)
TIMEZONE="America/Sao_Paulo"                                # IANA timezone (default: America/Sao_Paulo)
LOG_PATH="."                                                # log directory
AUDIO_TRANSCRIBE_MODEL="gpt-4o-mini-transcribe"             # audio transcription model
```

**Optional assistant tuning:**

```env
ASSISTANT_MEMORY_PATH="./assistant_memory.sqlite3"
ASSISTANT_MAX_MESSAGES_PER_SESSION="300"
ASSISTANT_MAX_TOOL_CALLS_PER_SESSION="300"
ASSISTANT_MAX_HISTORY_CHARS="12000"
ASSISTANT_MAX_TOOL_OUTPUT_CHARS="8000"
```

> Email settings (`EMAIL_FROM`, `EMAIL_TO`, `DISPLAY_NAME`, etc.) can be set in `.env` as defaults or configured per-user via the assistant chat.

### 4) Create a user

```sh
python -m web_app.manage_users create --username carlos --password <password> [--display-name "Carlos"]
python -m web_app.manage_users list
```

## Run

```sh
python run_web.py
```

The app starts at `http://localhost:8001` by default.

## Tests

```sh
./env/bin/python -m pytest -q
```

- **750+ tests** with a minimum coverage threshold of **80%**.
- External network calls are blocked by default (`tests/conftest.py`).
- Coverage scope: `assistant_connector`, `gmail_connector`, `openai_connector`, `calendar_connector`, `web_app`, `utils`.

## User credential management

Integration credentials (email settings, etc.) are stored **per-user and encrypted** in SQLite. Users can configure them via the assistant chat:

> *"configure email_from: me@example.com"*

| Key | Description |
|-----|-------------|
| `email_from` | Gmail address to send from |
| `email_to` | Default email recipient |
| `display_name` | Display name used in emails |
| `email_tone` | Tone for assistant-written emails |
| `email_signature` | Signature appended to emails |
| `email_style_guide` | Style instructions for email composition |
| `email_subject_prefix` | Prefix added to email subjects |

Google (Gmail + Calendar) credentials are managed via the OAuth flow in the web app settings.

## Deploy (production)

- **Stack:** Ubuntu 22.04 + Nginx (SSL/Let's Encrypt) + systemd + uvicorn
- **Deploy:** `sudo ./deploy/deploy_web_app.sh` (idempotent)
- **Service:** `deploy/systemd/personal-assistant-web.service`

```sh
sudo cp deploy/systemd/personal-assistant-web.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now personal-assistant-web
```
