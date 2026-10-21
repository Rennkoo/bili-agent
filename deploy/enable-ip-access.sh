#!/usr/bin/env bash
set -Eeuo pipefail

# Temporary IP access for a server without a domain. Use HTTPS with Caddy before
# exposing this service for regular or sensitive use.
if [[ "$(id -u)" -ne 0 ]]; then
  echo "请使用 root 执行此脚本。" >&2
  exit 1
fi

APP_DIR="${BILI_AGENT_DIR:-/opt/bili-agent}"
SERVICE_FILE="/etc/systemd/system/bili-agent.service"
TOKEN_FILE="/root/bili-agent-web-token.txt"

if [[ ! -f "${APP_DIR}/.env" || ! -f "${SERVICE_FILE}" ]]; then
  echo "未找到 ${APP_DIR}/.env 或 ${SERVICE_FILE}，请先完成基础部署。" >&2
  exit 1
fi

if grep -q '^WEB_AUTH_TOKEN=.' "${APP_DIR}/.env"; then
  token="$(sed -n 's/^WEB_AUTH_TOKEN=//p' "${APP_DIR}/.env" | head -n 1)"
else
  token="$(openssl rand -hex 32)"
  printf '\nWEB_AUTH_TOKEN=%s\n' "${token}" >> "${APP_DIR}/.env"
fi

chmod 600 "${APP_DIR}/.env"
printf '%s\n' "${token}" > "${TOKEN_FILE}"
chmod 600 "${TOKEN_FILE}"

cp "${SERVICE_FILE}" "${SERVICE_FILE}.bak.$(date -u +%Y%m%dT%H%M%SZ)"
sed -i -E 's|^ExecStart=.*bili-agent web --host [^ ]+ --port [0-9]+.*$|ExecStart=/opt/bili-agent/.venv/bin/bili-agent web --host 0.0.0.0 --port 8765|' "${SERVICE_FILE}"

if command -v firewall-cmd >/dev/null 2>&1 && systemctl is-active --quiet firewalld; then
  firewall-cmd --permanent --add-port=8765/tcp
  firewall-cmd --reload
elif command -v ufw >/dev/null 2>&1 && ufw status | grep -q active; then
  ufw allow 8765/tcp
fi

systemctl daemon-reload
systemctl restart bili-agent.service
sleep 1
curl --fail --silent http://127.0.0.1:8765/api/health >/tmp/bili-agent-health.json

echo "服务已绑定 0.0.0.0:8765。"
echo "公网地址：http://$(hostname -I | awk '{print $1}'):8765/"
echo "Bearer Token 已保存到：${TOKEN_FILE}"
echo "还需要在阿里云安全组/轻量服务器防火墙放行 TCP 8765。"
cat /tmp/bili-agent-health.json
echo
