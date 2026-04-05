#!/usr/bin/env bash
# =============================================================================
# deploy_web_app.sh — Deploy completo do Personal Assistant Web App
# Domínio: app.carlosplf.com
# Ubuntu Server 22.04+
#
# Uso:
#   chmod +x deploy_web_app.sh
#   sudo ./deploy_web_app.sh
#
# O script é idempotente: pode ser executado novamente para atualizar
# o código sem reconfigurar do zero.
# =============================================================================

set -euo pipefail

# ---- Configurações ---- (ajuste se necessário)
DOMAIN="app.carlosplf.com"
APP_USER="carlos"
APP_DIR="/home/${APP_USER}/Projects/personal-assistant"
REPO_URL="https://github.com/carlosplf/personal-assistant.git"   # ajuste se necessário
NGINX_CONF="/etc/nginx/sites-available/${DOMAIN}"
SERVICE_NAME="personal-assistant-web"
PYTHON_BIN="${APP_DIR}/env/bin/python"
CERTBOT_EMAIL="carlos@carlosplf.com"   # ajuste para seu e-mail

# ---- Cores para output ----
GREEN="\033[0;32m"
YELLOW="\033[1;33m"
RED="\033[0;31m"
RESET="\033[0m"

info()    { echo -e "${GREEN}[INFO]${RESET} $*"; }
warning() { echo -e "${YELLOW}[WARN]${RESET} $*"; }
error()   { echo -e "${RED}[ERROR]${RESET} $*"; exit 1; }

# ---- Verificações iniciais ----
[[ $EUID -ne 0 ]] && error "Execute como root: sudo ./deploy_web_app.sh"
id "${APP_USER}" &>/dev/null || error "Usuário '${APP_USER}' não existe no sistema."

# =============================================================================
# 1. Dependências do sistema
# =============================================================================
info "Instalando dependências do sistema..."
apt-get update -qq
apt-get install -y -qq \
    python3 python3-pip python3-venv \
    nginx \
    certbot python3-certbot-nginx \
    git \
    curl \
    build-essential \
    libffi-dev

# =============================================================================
# 2. Código da aplicação
# =============================================================================
if [[ -d "${APP_DIR}/.git" ]]; then
    info "Atualizando código existente em ${APP_DIR}..."
    sudo -u "${APP_USER}" git -C "${APP_DIR}" pull --ff-only
else
    info "Clonando repositório em ${APP_DIR}..."
    sudo -u "${APP_USER}" git clone "${REPO_URL}" "${APP_DIR}"
fi

# =============================================================================
# 3. Ambiente virtual e dependências Python
# =============================================================================
info "Configurando ambiente virtual Python..."
if [[ ! -d "${APP_DIR}/env" ]]; then
    sudo -u "${APP_USER}" python3 -m venv "${APP_DIR}/env"
fi

info "Instalando dependências Python..."
sudo -u "${APP_USER}" "${APP_DIR}/env/bin/pip" install --quiet --upgrade pip
sudo -u "${APP_USER}" "${APP_DIR}/env/bin/pip" install --quiet -r "${APP_DIR}/requirements.txt"

# =============================================================================
# 4. Arquivo .env
# =============================================================================
if [[ ! -f "${APP_DIR}/.env" ]]; then
    warning "Arquivo .env não encontrado. Criando template em ${APP_DIR}/.env"
    warning "Edite o arquivo e execute o script novamente ou reinicie o serviço."
    cat > "${APP_DIR}/.env" << 'EOF'
# Personal Assistant — Variáveis de ambiente
# Preencha todos os valores antes de iniciar o serviço.

# Web JWT
WEB_JWT_SECRET=TROQUE_POR_UM_SECRET_FORTE
WEB_JWT_EXPIRY_HOURS=72

# Web server
WEB_HOST=127.0.0.1
WEB_PORT=8000

# Notion
NOTION_API_KEY=
NOTION_DATABASE_ID=

# OpenAI
OPENAI_KEY=

# Gmail
EMAIL_FROM=
EMAIL_TO=
DISPLAY_NAME=

# Telegram (se estiver usando o bot em paralelo)
TELEGRAM_BOT_TOKEN=
TELEGRAM_ALLOWED_USER_IDS=

# Outros
LOG_PATH=/home/carlos/Projects/personal-assistant/log_file.txt
CREDENTIAL_ENCRYPTION_KEY=
EOF
    chown "${APP_USER}:${APP_USER}" "${APP_DIR}/.env"
    chmod 600 "${APP_DIR}/.env"
fi

# =============================================================================
# 5. Nginx — configuração HTTP inicial (para o certbot funcionar)
# =============================================================================
info "Configurando Nginx (HTTP temporário para certbot)..."
cat > "${NGINX_CONF}" << EOF
server {
    listen 80;
    server_name ${DOMAIN};

    # Permite que o certbot valide o domínio
    location /.well-known/acme-challenge/ {
        root /var/www/html;
    }

    location / {
        return 301 https://\$host\$request_uri;
    }
}
EOF

ln -sf "${NGINX_CONF}" "/etc/nginx/sites-enabled/${DOMAIN}"

# Remove o site default se ainda estiver ativo
rm -f /etc/nginx/sites-enabled/default

nginx -t && systemctl reload nginx
info "Nginx recarregado com configuração HTTP."

# =============================================================================
# 6. Let's Encrypt — obtenção/renovação do certificado
# =============================================================================
if [[ -d "/etc/letsencrypt/live/${DOMAIN}" ]]; then
    info "Certificado já existe. Tentando renovar..."
    certbot renew --nginx --non-interactive --quiet || warning "Renovação falhou ou não era necessária."
else
    info "Obtendo certificado Let's Encrypt para ${DOMAIN}..."
    certbot certonly \
        --nginx \
        --non-interactive \
        --agree-tos \
        --email "${CERTBOT_EMAIL}" \
        -d "${DOMAIN}" \
        || error "Falha ao obter certificado. Verifique se o DNS de '${DOMAIN}' aponta para este servidor."
fi

# =============================================================================
# 7. Nginx — configuração HTTPS final
# =============================================================================
info "Configurando Nginx com HTTPS..."
cat > "${NGINX_CONF}" << EOF
# Redireciona HTTP → HTTPS
server {
    listen 80;
    server_name ${DOMAIN};
    return 301 https://\$host\$request_uri;
}

server {
    listen 443 ssl http2;
    server_name ${DOMAIN};

    # Certificados Let's Encrypt
    ssl_certificate     /etc/letsencrypt/live/${DOMAIN}/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/${DOMAIN}/privkey.pem;

    # Configurações SSL modernas (Mozilla Intermediate)
    ssl_protocols       TLSv1.2 TLSv1.3;
    ssl_ciphers         ECDHE-ECDSA-AES128-GCM-SHA256:ECDHE-RSA-AES128-GCM-SHA256:ECDHE-ECDSA-AES256-GCM-SHA384:ECDHE-RSA-AES256-GCM-SHA384;
    ssl_prefer_server_ciphers off;
    ssl_session_cache   shared:SSL:10m;
    ssl_session_timeout 1d;
    ssl_stapling        on;
    ssl_stapling_verify on;

    # Headers de segurança
    add_header Strict-Transport-Security "max-age=63072000; includeSubDomains; preload" always;
    add_header X-Content-Type-Options    nosniff always;
    add_header X-Frame-Options           DENY always;
    add_header Referrer-Policy           strict-origin-when-cross-origin always;
    add_header Permissions-Policy        "camera=(), microphone=(self), geolocation=()" always;

    # PWA — cache de assets estáticos
    location /static/ {
        proxy_pass http://127.0.0.1:8000/static/;
        proxy_cache_valid 200 1d;
        add_header Cache-Control "public, max-age=86400, immutable";
        proxy_set_header Host \$host;
    }

    # Proxy para o FastAPI
    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Host              \$host;
        proxy_set_header X-Real-IP         \$remote_addr;
        proxy_set_header X-Forwarded-For   \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_set_header Upgrade           \$http_upgrade;
        proxy_set_header Connection        "upgrade";

        # Suporte a uploads e respostas longas do assistente
        client_max_body_size  25M;
        proxy_read_timeout    180s;
        proxy_connect_timeout 10s;
        proxy_send_timeout    60s;
    }
}
EOF

nginx -t && systemctl reload nginx
info "Nginx configurado com HTTPS."

# =============================================================================
# 8. Renovação automática do certificado (cron)
# =============================================================================
CRON_JOB="0 3 * * * certbot renew --nginx --quiet"
if ! crontab -l 2>/dev/null | grep -qF "certbot renew"; then
    info "Adicionando cron de renovação automática do certificado..."
    (crontab -l 2>/dev/null; echo "${CRON_JOB}") | crontab -
fi

# =============================================================================
# 9. Systemd — serviço da aplicação web
# =============================================================================
info "Instalando serviço systemd '${SERVICE_NAME}'..."
cat > "/etc/systemd/system/${SERVICE_NAME}.service" << EOF
[Unit]
Description=Personal Assistant Web PWA
After=network.target

[Service]
Type=simple
User=${APP_USER}
WorkingDirectory=${APP_DIR}
EnvironmentFile=${APP_DIR}/.env
ExecStart=${PYTHON_BIN} ${APP_DIR}/run_web.py
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable "${SERVICE_NAME}"
systemctl restart "${SERVICE_NAME}"

# Aguarda alguns segundos e verifica se o serviço está rodando
sleep 3
if systemctl is-active --quiet "${SERVICE_NAME}"; then
    info "Serviço '${SERVICE_NAME}' está rodando."
else
    error "Serviço '${SERVICE_NAME}' falhou ao iniciar. Verifique: journalctl -u ${SERVICE_NAME} -n 50"
fi

# =============================================================================
# 10. Verificação final
# =============================================================================
info "Verificando resposta da aplicação..."
HTTP_STATUS=$(curl -sk -o /dev/null -w "%{http_code}" "https://${DOMAIN}/api/health" || echo "000")
if [[ "${HTTP_STATUS}" == "200" ]]; then
    info "✅ Deploy concluído com sucesso!"
    info "   URL: https://${DOMAIN}"
else
    warning "⚠️  A aplicação respondeu com status ${HTTP_STATUS}."
    warning "   Verifique os logs: journalctl -u ${SERVICE_NAME} -f"
    warning "   E o arquivo .env: ${APP_DIR}/.env"
fi

echo ""
echo -e "${GREEN}========================================${RESET}"
echo -e "${GREEN}  Deploy finalizado!${RESET}"
echo -e "${GREEN}  https://${DOMAIN}${RESET}"
echo -e "${GREEN}========================================${RESET}"
echo ""
echo "Comandos úteis:"
echo "  Ver logs do app:      journalctl -u ${SERVICE_NAME} -f"
echo "  Reiniciar serviço:    sudo systemctl restart ${SERVICE_NAME}"
echo "  Status do serviço:    sudo systemctl status ${SERVICE_NAME}"
echo "  Renovar certificado:  sudo certbot renew"
echo ""
echo "Para criar o primeiro usuário web:"
echo "  sudo -u ${APP_USER} ${PYTHON_BIN} ${APP_DIR}/web_app/manage_users.py create <username>"
