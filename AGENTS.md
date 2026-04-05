# AGENTS.md — Project Scope (Personal assistant)

Este arquivo complementa `/home/carlos/AGENTS.md` com regras específicas deste projeto.

## Objetivo do projeto
- Integrar Notion + OpenAI + Gmail para enviar, por email, um resumo das tarefas com prazo próximo.
- Fluxo principal: coletar tarefas no Notion -> gerar resumo com OpenAI -> enviar email formatado.

## Estrutura principal
- `run.py`: orquestração do fluxo completo (Telegram bot).
- `run_web.py`: entry point do web app PWA (FastAPI + uvicorn).
- `web_app/`: interface web PWA (FastAPI, auth JWT, frontend vanilla JS).
- `notion_connector/`: coleta e filtro das tarefas no Notion.
- `openai_connector/`: geração de resumo/conteúdo com OpenAI.
- `gmail_connector/`: autenticação e envio de email.
- `utils/`: logger, carregamento de credenciais e parsing/formatação auxiliar.
- `templates/email_template.html`: template do corpo do email.

## Convenções de implementação
1. Mantenha separação de responsabilidades por conector (Notion/OpenAI/Gmail).
2. Evite lógica de integração diretamente em `run.py`; prefira funções nos módulos de conector/utilitários.
3. Nunca hardcode credenciais; sempre use variáveis de ambiente (`.env`).
4. Preserve logs úteis para diagnóstico (sucesso e erro) usando o logger do projeto.
5. Comentários em código devem ser em inglês.

## Execução local
```bash
python3 -m venv ./env
source ./env/bin/activate
pip install -r requirements.txt

# Telegram bot
python run.py

# Web app PWA
python run_web.py
```

## Gerenciamento de usuários web
```bash
python -m web_app.manage_users create --username carlos --password <senha>
python -m web_app.manage_users list
```

## Configuração esperada
- Arquivos/segredos locais (não versionar):
  - `.env` com `NOTION_DATABASE_ID`, `NOTION_API_KEY`, `OPENAI_KEY`, `EMAIL_FROM`, `EMAIL_TO`, `DISPLAY_NAME`, `LOG_PATH`
  - `.env` com `WEB_JWT_SECRET`, `WEB_JWT_EXPIRY_HOURS`, `WEB_HOST`, `WEB_PORT` (web app)
  - `credentials.json` (Google OAuth)
  - `token.json` (gerado após autenticação Gmail)

## Validação de mudanças
- Se a mudança não exigir APIs externas, prefira validação local isolada da função/módulo alterado.
- Se alterar fluxo de envio, validar modo de teste para evitar disparo real de emails sempre que possível.
- Ao adicionar comportamento novo, incluir testes em `tests/` quando viável.
