#!/usr/bin/env bash
set -Eeuo pipefail

if [[ "$(id -u)" -ne 0 ]]; then
  echo "Run this installer as root." >&2
  exit 1
fi

APP_DIR="${BILI_AGENT_DIR:-/opt/bili-agent}"
REPO_URL="${BILI_AGENT_REPO:-https://github.com/Rennkoo/bili-agent.git}"

dnf -y install git curl wget dnf-plugin-releasever-adapter --repo alinux3-plus || dnf -y install git curl wget

if ! command -v docker >/dev/null 2>&1; then
  wget -O /etc/yum.repos.d/docker-ce.repo \
    http://mirrors.cloud.aliyuncs.com/docker-ce/linux/centos/docker-ce.repo
  sed -i 's|https://mirrors.aliyun.com|http://mirrors.cloud.aliyuncs.com|g' \
    /etc/yum.repos.d/docker-ce.repo
  dnf -y install docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
  systemctl enable --now docker
fi

if [[ -d "${APP_DIR}/.git" ]]; then
  git -C "${APP_DIR}" fetch origin
  git -C "${APP_DIR}" reset --hard origin/main
else
  git clone "${REPO_URL}" "${APP_DIR}"
fi

cd "${APP_DIR}"
mkdir -p data/audio data/media
[[ -f .env ]] || cp .env.example .env
chmod 600 .env

set_env() {
  local key="$1" value="$2"
  if grep -q "^${key}=" .env; then
    sed -i "s|^${key}=.*|${key}=${value}|" .env
  else
    printf '%s=%s\n' "${key}" "${value}" >> .env
  fi
}

# This server has 1 GiB RAM. Keep the first deployment lightweight and local-only.
set_env BILI_AGENT_EXTRAS ""
set_env ASR_ENABLED "false"
set_env STORAGE_DB_PATH "/data/bili-agent.sqlite3"
set_env ASR_CACHE_DIR "/data/audio"
set_env MEDIA_CACHE_DIR "/data/media"
set_env WEB_AUTH_TOKEN ""

docker compose up -d --build
docker compose ps
curl --fail --silent http://127.0.0.1:8765/api/health
echo
echo "bili-agent is running locally on ${APP_DIR}. Configure a domain and HTTPS before exposing it publicly."
