#!/usr/bin/env bash
set -euo pipefail

# ================= BASIC CONFIG =================

APP_DIR="/opt/tgbot"
ENV_NAME="tgbot"
PY_VER="3.11"

APP_HOST="127.0.0.1"
APP_PORT="8000"

TZ_VAL="UTC"
LOG_LEVEL="INFO"

DATA_DIR="$APP_DIR/data"
EXPORT_DIR="$DATA_DIR/exports"

MINICONDA="/root/miniconda3"
CONDA_BIN="$MINICONDA/bin/conda"
PIP_BIN="$MINICONDA/envs/$ENV_NAME/bin/pip"
PY_BIN="$MINICONDA/envs/$ENV_NAME/bin/python"

NGINX_SITE="/etc/nginx/sites-available/tgbot.conf"
NGINX_ENABLED="/etc/nginx/sites-enabled/tgbot.conf"

ENV_FILE="$APP_DIR/.env"


# ================= UTILS =================

log() { echo "[DEPLOY] $*"; }
die() { echo "[ERROR] $*" >&2; exit 1; }

need_root() {
  [[ "$(id -u)" == "0" ]] || die "Please run with sudo"
}

rand_secret() {
  python3 - <<'PY'
import os,base64
print(base64.urlsafe_b64encode(os.urandom(48)).decode().rstrip("="))
PY
}

# IMPORTANT: prompts go to stderr so they never end up inside .env
ask() {
  local name="$1"
  local example="$2"
  local v=""

  while [[ -z "$v" ]]; do
    echo >&2
    echo ">>> Input $name (example: $example)" >&2
    echo ">>> Copy & paste, then Enter:" >&2
    read -r v
    v="$(echo "$v" | xargs)"
  done

  echo "$v"
}

ask_yn() {
  local prompt="$1"
  local default="${2:-N}" # Y or N
  local ans=""

  while true; do
    echo >&2
    if [[ "$default" == "Y" ]]; then
      echo -n ">>> $prompt [Y/n]: " >&2
    else
      echo -n ">>> $prompt [y/N]: " >&2
    fi
    read -r ans || true
    ans="$(echo "${ans:-}" | tr '[:upper:]' '[:lower:]' | xargs)"

    if [[ -z "$ans" ]]; then
      [[ "$default" == "Y" ]] && return 0 || return 1
    fi
    case "$ans" in
      y|yes) return 0 ;;
      n|no) return 1 ;;
      *) echo "Please input y or n." >&2 ;;
    esac
  done
}

# ================= SYSTEM =================

apt_install() {
  if ! dpkg -s "$1" >/dev/null 2>&1; then
    log "Install: $1"
    apt install -y "$1"
  fi
}

install_base() {
  log "Install system deps"
  apt update
  apt_install git
  apt_install curl
  apt_install nginx
  apt_install certbot
  apt_install python3-certbot-nginx
  apt_install python3
  apt_install ca-certificates
}

install_node() {
  if command -v node >/dev/null 2>&1; then
    local v
    v="$(node -v | sed 's/v//')"
    if [[ "${v%%.*}" -ge 18 ]]; then
      log "Node OK: v$v"
      return
    fi
  fi

  log "Install Node.js 18"
  curl -fsSL https://deb.nodesource.com/setup_18.x | bash -
  apt install -y nodejs
}

install_pm2() {
  if command -v pm2 >/dev/null 2>&1; then
    log "PM2 OK"
    return
  fi
  log "Install PM2"
  npm i -g pm2
}

install_conda() {
  if [[ -x "$CONDA_BIN" ]]; then
    log "Conda OK"
    return
  fi

  log "Install Miniconda"
  local TMP="/tmp/miniconda.sh"
  curl -fsSL -o "$TMP" https://repo.anaconda.com/miniconda/Miniconda3-latest-Linux-x86_64.sh
  bash "$TMP" -b -p "$MINICONDA"
  rm -f "$TMP"
}

create_conda_env() {
  if [[ -x "$PY_BIN" ]]; then
    log "Conda env OK"
    return
  fi

  log "Create conda env: $ENV_NAME"
  "$CONDA_BIN" create -y -n "$ENV_NAME" python="$PY_VER"
  "$PIP_BIN" install -U pip
}

# ================= PROJECT =================

prepare_dirs() {
  log "Prepare dirs"
  mkdir -p "$DATA_DIR" "$EXPORT_DIR" "$APP_DIR/logs"
  chmod 700 "$DATA_DIR" "$EXPORT_DIR" "$APP_DIR/logs" || true
}

prepare_env() {
  if [[ -f "$ENV_FILE" ]]; then
    log ".env exists (skip)"
    chmod 600 "$ENV_FILE" || true
    return
  fi

  echo >&2
  echo "==============================" >&2
  echo "   CONFIG REQUIRED" >&2
  echo "==============================" >&2

  local DOMAIN BOT_TOKEN OWNER_ID SECRET
  DOMAIN="$(ask DOMAIN tg.example.com)"
  BOT_TOKEN="$(ask BOT_TOKEN 123456:xxxx)"
  OWNER_ID="$(ask OWNER_ID 123456789)"
  SECRET="$(rand_secret)"

  log "Generate .env"

  cat >"$ENV_FILE" <<EOF
APP_ENV=production
LOG_LEVEL=$LOG_LEVEL
TZ=$TZ_VAL

BOT_TOKEN=$BOT_TOKEN
OWNER_ID=$OWNER_ID
SESSION_HMAC_SECRET=$SECRET

WEBAPP_ALLOWED_ORIGINS=https://$DOMAIN
WEBAPP_AUTH_MAX_AGE_SEC=300
SESSION_TTL_SEC=1800

APP_HOST=$APP_HOST
APP_PORT=$APP_PORT

DATA_DIR=$DATA_DIR
EXPORT_DIR=$EXPORT_DIR

TRUST_PROXY_HEADERS=true
ORIGIN_CHECK_STRICT=true
EOF

  chmod 600 "$ENV_FILE"
}

install_python_deps() {
  log "Install Python deps"
  "$PIP_BIN" install -r "$APP_DIR/requirements.txt"
}

build_frontend() {
  log "Build frontend"
  cd "$APP_DIR/miniapp"
  npm ci
  npm run build
  cd "$APP_DIR"
}

# ================= NGINX =================

get_domain() {
  grep -E '^WEBAPP_ALLOWED_ORIGINS=' "$ENV_FILE" \
    | cut -d= -f2- \
    | sed 's|^https\?://||' \
    | tr -d '\r' \
    | xargs
}

write_nginx_http_only() {
  local DOMAIN
  DOMAIN="$(get_domain)"
  [[ -n "$DOMAIN" ]] || die "Cannot read DOMAIN from $ENV_FILE"

  log "Write nginx config (HTTP-only bootstrap) -> $NGINX_SITE"

  cat >"$NGINX_SITE" <<EOF
upstream tgbot_backend {
    server 127.0.0.1:$APP_PORT;
    keepalive 16;
}

server {
    listen 80;
    listen [::]:80;
    server_name $DOMAIN;

    root $APP_DIR/miniapp/dist;
    index index.html;

    location /ws {
        proxy_pass http://tgbot_backend/ws;

        proxy_http_version 1.1;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "upgrade";

        proxy_set_header Host \$host;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto http;

        proxy_read_timeout 75s;
        proxy_buffering off;
    }

    location /assets/ {
        try_files \$uri =404;
        expires 7d;
        add_header Cache-Control "public, immutable";
    }

    location / {
        try_files \$uri /index.html;
    }

    location ~ /\.(?!well-known).* {
        deny all;
    }
}
EOF
}

write_nginx_https() {
  local DOMAIN="$1"
  [[ -n "$DOMAIN" ]] || die "DOMAIN empty"

  log "Write nginx config (HTTPS) -> $NGINX_SITE"

  cat >"$NGINX_SITE" <<EOF
upstream tgbot_backend {
    server 127.0.0.1:$APP_PORT;
    keepalive 16;
}

server {
    listen 80;
    listen [::]:80;
    server_name $DOMAIN;
    return 301 https://\$host\$request_uri;
}

server {
    listen 443 ssl http2;
    listen [::]:443 ssl http2;
    server_name $DOMAIN;

    ssl_certificate /etc/letsencrypt/live/$DOMAIN/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/$DOMAIN/privkey.pem;

    root $APP_DIR/miniapp/dist;
    index index.html;

    location /ws {
        proxy_pass http://tgbot_backend/ws;

        proxy_http_version 1.1;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "upgrade";

        proxy_set_header Host \$host;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto https;

        proxy_read_timeout 75s;
        proxy_buffering off;
    }

    location /assets/ {
        try_files \$uri =404;
        expires 7d;
        add_header Cache-Control "public, immutable";
    }

    location / {
        try_files \$uri /index.html;
    }

    location ~ /\.(?!well-known).* {
        deny all;
    }
}
EOF
}

enable_nginx() {
  if [[ ! -e "$NGINX_ENABLED" ]]; then
    ln -s "$NGINX_SITE" "$NGINX_ENABLED"
  fi
  nginx -t
  systemctl reload nginx
}

cert_exists() {
  local d="$1"
  [[ -d "/etc/letsencrypt/live/$d" ]] \
    && [[ -f "/etc/letsencrypt/live/$d/fullchain.pem" ]] \
    && [[ -f "/etc/letsencrypt/live/$d/privkey.pem" ]]
}

maybe_install_cert_and_enable_https() {
  local env_domain chosen_domain
  env_domain="$(get_domain)"
  [[ -n "$env_domain" ]] || die "Cannot read DOMAIN from $ENV_FILE"

  echo >&2
  echo "==============================" >&2
  echo " HTTPS (Let's Encrypt)" >&2
  echo "==============================" >&2
  echo "Current DOMAIN from .env: $env_domain" >&2
  echo "Requirement: DNS A record points here + 80/443 open." >&2

  if ! ask_yn "Request HTTPS certificate now?" "N"; then
    log "Skip HTTPS cert. (HTTP-only nginx is active)"
    return
  fi

  if ask_yn "Use a different domain than .env?" "N"; then
    chosen_domain="$(ask DOMAIN "$env_domain")"
  else
    chosen_domain="$env_domain"
  fi

  if cert_exists "$chosen_domain"; then
    log "Cert already exists for $chosen_domain"
  else
    log "Request cert for: $chosen_domain"
    certbot --nginx -d "$chosen_domain" || die "Certbot failed"
  fi

  if cert_exists "$chosen_domain"; then
    write_nginx_https "$chosen_domain"
    nginx -t
    systemctl reload nginx
    log "HTTPS enabled for $chosen_domain"
  else
    die "Cert still not found: /etc/letsencrypt/live/$chosen_domain"
  fi
}

# ================= PM2 =================

start_pm2() {
  log "Start PM2"
  cd "$APP_DIR"
  pm2 start ecosystem.config.js --update-env
  pm2 save
}

# ================= MAIN =================

main() {
  need_root
  [[ -d "$APP_DIR" ]] || die "APP_DIR not found: $APP_DIR"

  install_base
  install_node
  install_pm2
  install_conda
  create_conda_env

  prepare_dirs
  prepare_env

  install_python_deps
  build_frontend

  # nginx bootstrap (HTTP-only)
  write_nginx_http_only
  enable_nginx

  # optional HTTPS
  maybe_install_cert_and_enable_https

  start_pm2

  echo
  echo "================================"
  echo " DEPLOY SUCCESS"
  echo "================================"
  echo
  echo "Check status:"
  echo "  pm2 ls"
  echo "  pm2 logs tgbot"
  echo
}

main "$@"