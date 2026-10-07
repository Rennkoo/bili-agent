# bili-agent 线上部署方案

## 1. 推荐架构

当前 Web 层使用标准库 `ThreadingHTTPServer`，SQLite 可以保存会话和任务状态，但分析任务仍在当前进程内执行。因此第一阶段使用单台 Linux 云主机、单个 Docker 容器、单副本，不要直接运行多个副本。

```text
浏览器 --HTTPS--> Caddy/Nginx --本机--> bili-agent:8765
                                      |
                                      +-- ./data/bili-agent.sqlite3
                                      +-- ./data/audio
                                      +-- ./data/media
```

建议资源：字幕分析至少 2 vCPU、4 GB RAM、20 GB 磁盘；CPU ASR 建议 4 vCPU、8 GB RAM、50 GB 以上磁盘。生产公网只开放 80/443，8765 仅绑定 `127.0.0.1`。

## 2. 首次部署

以下命令以 Ubuntu 22.04/24.04 为例：

```bash
sudo apt-get update
sudo apt-get install -y docker.io docker-compose-plugin ufw
sudo systemctl enable --now docker
sudo ufw allow OpenSSH
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw --force enable
sudo mkdir -p /opt/bili-agent
sudo chown "$USER":"$USER" /opt/bili-agent
git clone https://github.com/Rennkoo/bili-agent.git /opt/bili-agent
cd /opt/bili-agent
mkdir -p data/audio data/media
cp .env.example .env
chmod 600 .env
```

编辑 `.env`，公网环境至少设置：

```dotenv
LLM_API_KEY=替换为真实密钥
LLM_BASE_URL=https://api.openai.com/v1
LLM_MODEL=gpt-4o-mini
WEB_AUTH_TOKEN=至少32位随机字符串
ASR_ENABLED=true
ASR_MODEL=base
ASR_DEVICE=cpu
ASR_COMPUTE_TYPE=int8
STORAGE_DB_PATH=/data/bili-agent.sqlite3
ASR_CACHE_DIR=/data/audio
MEDIA_CACHE_DIR=/data/media
```

`.env`、API Key、B 站 Cookie 和真实分析结果不得提交到 GitHub。兼容接口只需替换 `LLM_BASE_URL` 与 `LLM_MODEL`。

启动并检查：

```bash
docker compose up -d --build
docker compose ps
curl --fail http://127.0.0.1:8765/api/health
docker compose logs --tail=200 bili-agent
```

健康响应重点看 `ready: true`、`llm_configured: true`、`capabilities.asr` 和 `capabilities.ffmpeg`。`warnings` 是能力降级提示，不一定表示启动失败。

## 3. HTTPS 和双层鉴权

仓库提供 `deploy/Caddyfile.example`。Caddy 负责 HTTPS 和第一层登录，应用的 `WEB_AUTH_TOKEN` 负责第二层 Bearer Token 保护。

```bash
sudo apt-get install -y caddy
sudo caddy hash-password --plaintext "替换为管理密码"
sudo cp deploy/Caddyfile.example /etc/caddy/Caddyfile
sudo nano /etc/caddy/Caddyfile
sudo caddy validate --config /etc/caddy/Caddyfile
sudo systemctl enable --now caddy
sudo systemctl reload caddy
```

把样例中的域名和密码哈希替换成真实值。验证：

```bash
curl --fail https://video.example.com/api/health
curl -i https://video.example.com/api/settings
curl --fail -H "Authorization: Bearer ${WEB_AUTH_TOKEN}" https://video.example.com/api/settings
```

不要把包含 `Authorization` 的命令或 `.env` 内容贴到日志、Issue、截图或聊天中。

## 4. 持久化和备份

Compose 使用 `./data:/data`：SQLite 位于 `data/bili-agent.sqlite3`，音频缓存位于 `data/audio`，视频和关键帧位于 `data/media`。Compose 会强制把数据库写入 `/data`，即使 `.env` 忘记修改也不会把数据库留在容器层。缓存可以清理，SQLite 和 `.env` 必须备份。

如果是从旧版本的 named volume 升级，先停止服务并把旧卷复制到新目录（卷名以实际 `docker volume ls` 输出为准）：

```bash
docker compose down
mkdir -p data
docker run --rm -v bili-agent_bili-agent-data:/from -v "$(pwd)/data:/to" alpine sh -c 'cp -a /from/. /to/'
```

仓库提供 `deploy/backup.sh`，默认排除大型音频和视频缓存：

```bash
cd /opt/bili-agent
./deploy/backup.sh /opt/backups/bili-agent
chmod -R go-rwx /opt/backups/bili-agent
```

建议每日备份、保留 7 份，并把备份同步到另一台机器或对象存储。恢复前停止容器：

```bash
docker compose down
tar -xzf /opt/backups/bili-agent/<备份文件>.tar.gz -C /opt/bili-agent
docker compose up -d
```

排查资源和日志：

```bash
docker compose logs --since=30m bili-agent
df -h /opt/bili-agent
du -sh data/audio data/media data/bili-agent.sqlite3
```

## 5. 发布、升级和回滚

只从 `main` 的版本标签发布：

```bash
git checkout main
git pull --ff-only
git tag -a v0.1.1 -m "release: v0.1.1"
git push origin main --tags
```

服务器升级：

```bash
cd /opt/bili-agent
./deploy/backup.sh /opt/backups/bili-agent
git fetch --tags origin
git checkout v0.1.1
docker compose up -d --build
curl --fail http://127.0.0.1:8765/api/health
```

回滚到上一版本：

```bash
git checkout v0.1.0
docker compose up -d --build
curl --fail http://127.0.0.1:8765/api/health
docker compose logs --tail=200 bili-agent
```

回滚时不要删除 `data` 或 `.env`。缓存损坏只清理 `data/audio` 或 `data/media`，SQLite 先备份。

## 6. GHCR 镜像方案

默认 CI 会在 Python 3.10、3.11、3.12 上运行测试。推送 `v*.*.*` 标签时，`.github/workflows/docker-publish.yml` 会把固定版本镜像发布到 GHCR。

```bash
docker login ghcr.io
docker pull ghcr.io/rennkoo/bili-agent:v0.1.1
```

生产环境使用完整版本标签，不使用会漂移的 `latest`。首次使用 GHCR 时确认仓库 Actions 具有 `packages: write` 权限。

## 7. 上线验收

- [ ] `.env`、Cookie、缓存、数据库和真实结果未进入 Git；
- [ ] `docker compose ps` 显示 healthy；
- [ ] 外网无法直接访问 8765；
- [ ] 域名 HTTPS 有效且 HTTP 自动跳转；
- [ ] Caddy Basic Auth 和 `WEB_AUTH_TOKEN` 均生效；
- [ ] BV、av、完整链接均可解析；
- [ ] 有字幕、无字幕 ASR、无声音视觉分析各验证一次；
- [ ] 长视频能显示进度并支持取消；
- [ ] Markdown、JSON、一图流和时间戳问答各验证一次；
- [ ] 完成一次备份恢复演练，并记录回滚标签；
- [ ] 已配置磁盘、容器重启和 `/api/health` 监控。

## 8. 后续多人化改造

需要多人、高并发或多副本时，再迁移到 PostgreSQL/Redis，将 ASR、yt-dlp、OCR 和视觉任务拆到 worker 队列，把媒体放到对象存储，并将 Web 层迁移到 FastAPI/Uvicorn。之后再增加正式用户、权限、CSRF、限流、审计、指标和错误追踪。

在上述改造完成前，单机单容器是当前版本最稳定、最容易备份和回滚的线上形态。
