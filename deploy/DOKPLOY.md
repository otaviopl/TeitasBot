# Dokploy

Este projeto foi containerizado para rodar no Dokploy com dois serviços:

- `web`: FastAPI/PWA
- `bot`: Telegram bot

Os dois containers compartilham o mesmo volume `assistant_data`, que guarda:

- `/data/assistant_memory.sqlite3`
- `/data/memories`
- `/data/files`
- `/data/logs`

## Como subir

1. Crie um projeto do tipo `Docker Compose` no Dokploy.
2. Aponte para este repositório.
3. Use o arquivo `docker-compose.yml` da raiz.
4. Configure o domínio `teitas.com.br` no serviço `web`.
5. Garanta que a porta publicada do serviço `web` seja `8001`.

## Variáveis importantes

Defina no Dokploy pelo menos:

- `WEB_JWT_SECRET`
- `CREDENTIAL_ENCRYPTION_KEY`
- `OPENAI_KEY`
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_ALLOWED_USER_IDS`
- `GOOGLE_OAUTH_CALLBACK_URL=https://teitas.com.br/auth/google/callback`
- `GOOGLE_OAUTH_CREDENTIALS_PATH=/data/credentials.json`

Se usar Notion, Gmail, Calendar e outras integrações, configure também as variáveis já esperadas pelo projeto.

## credentials.json do Google

O OAuth web precisa do arquivo `credentials.json` do Google Cloud.

Monte esse arquivo no container em:

- `/data/credentials.json`

Se o Dokploy estiver usando mount por arquivo, aponte o destino exatamente para esse path.

## Observações

- O SQLite fica em volume persistente e é compartilhado entre `web` e `bot`.
- Para este projeto isso é aceitável como primeira versão, já que o banco roda no mesmo host Docker.
- Se no futuro houver mais carga ou necessidade de alta concorrência, o próximo passo natural é migrar para Postgres.
