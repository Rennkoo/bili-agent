# bili-agent 上线与 Git 方案

## 1. 当前版本的上线边界

当前 Web 面板使用标准库 \`ThreadingHTTPServer\`，任务状态和分析会话保存在进程内存中。因此 v0.1 推荐采用：

- 单台 Linux 云主机或 NAS；
- 单个 bili-agent 容器、单进程运行；
- Caddy/Nginx 负责 HTTPS、域名和访问控制；
- 本地磁盘保存 ASR/视频缓存；
- 不直接把 8765 端口暴露到公网。

当前不建议直接开多个副本，因为不同副本之间不会共享 \`SessionStore\` 和 \`JobStore\`。用户刷新、容器重启或副本切换后，正在分析的任务和问答会丢失。

## 2. 推荐的第一阶段部署

### 2.1 准备服务器

建议 Ubuntu 22.04/24.04，至少 2 vCPU、4 GB RAM、20 GB 可用磁盘。启用 ASR 时，首次运行会下载 faster-whisper 模型，建议预留更多磁盘和内存。

安装 Docker：

\`\`\`bash
sudo apt-get update
sudo apt-get install -y docker.io docker-compose-plugin
sudo systemctl enable --now docker
\`\`\`

### 2.2 获取代码并配置密钥

\`\`\`bash
git clone <你的仓库地址> bili-agent
cd bili-agent
cp .env.example .env
chmod 600 .env
\`\`\`

编辑 \`.env\`，至少填写：

\`\`\`dotenv
LLM_API_KEY=...
LLM_BASE_URL=https://api.openai.com/v1
LLM_MODEL=gpt-4o-mini
\`\`\`

\`.env\` 只存在服务器，不提交 Git，不写入镜像，不放进公开日志。B 站 Cookie 同样按密钥处理。

### 2.3 启动与检查

\`\`\`bash
docker compose up -d --build
docker compose ps
curl http://127.0.0.1:8765/api/health
docker compose logs -f --tail=200 bili-agent
\`\`\`

预期健康响应：

\`\`\`json
{"status":"ok","llm_configured":true}
\`\`\`

## 3. HTTPS 与访问控制

推荐让 Caddy/Nginx 监听 443，再反向代理到 \`127.0.0.1:8765\`。至少开启以下保护：

- HTTPS；
- 基础认证、VPN 或公司 SSO 之一；
- 防火墙只开放 80/443；
- 限制请求体大小和请求频率；
- 不把 B 站 Cookie、LLM Key 写入访问日志；
- 为 \`/api/analyze\` 设置较低并发上限，避免同时下载多个视频耗尽 CPU、内存和带宽。

Caddy 示例：

\`\`\`text
video.example.com {
    basicauth {
        analyst <生成的密码哈希>
    }
    reverse_proxy 127.0.0.1:8765
}
\`\`\`

## 4. Git 仓库方案

### 4.1 分支

- \`main\`：可部署、可回滚的稳定版本；
- \`develop\`：集成分支；
- \`feature/<name>\`：功能开发；
- \`fix/<name>\`：缺陷修复；
- \`hotfix/<name>\`：线上紧急修复。

生产发布只从 \`main\` 构建，并使用版本标签，例如 \`v0.1.0\`、\`v0.1.1\`。

### 4.2 第一次入库

\`\`\`bash
git init
git add .
git status
git commit -m "feat: initial bili-agent release"
git branch -M main
git remote add origin <你的远程仓库地址>
git push -u origin main
\`\`\`

入库前确认：

- \`git status\` 中没有 \`.env\`；
- 没有 \`runtime-deps/\`、\`.venv/\`、\`.bili-agent/\`、音频和视频缓存；
- 没有 API Key、B 站 Cookie、真实分析结果和个人日志；
- CI 可以在干净环境中安装依赖并运行测试。

### 4.3 提交和发布

\`\`\`bash
git checkout -b feature/xxx
git add src tests docs
git commit -m "feat: xxx"
git push -u origin feature/xxx
\`\`\`

合并前必须通过 CI。发布时：

\`\`\`bash
git checkout main
git pull --ff-only
git tag -a v0.1.0 -m "release: v0.1.0"
git push origin main --tags
\`\`\`

服务器发布：

\`\`\`bash
git fetch --tags origin
git checkout v0.1.0
docker compose up -d --build
curl http://127.0.0.1:8765/api/health
\`\`\`

## 5. 回滚

保留最近一个可用镜像和 Git tag。发布失败时：

\`\`\`bash
git checkout v0.0.9
docker compose up -d --build
docker compose logs --tail=200 bili-agent
\`\`\`

缓存目录可以保留；若怀疑缓存损坏，只清理 \`/data/audio\` 或 \`/data/media\`，不要删除 \`.env\`。

## 6. 第二阶段生产化改造

在需要多人使用或多副本扩展前，建议完成：

1. 将 \`SessionStore\`、\`JobStore\` 迁移到 Redis/PostgreSQL；
2. 使用 Celery、RQ 或独立 worker 执行 ASR、yt-dlp 和视觉任务；
3. 将 Markdown、JSON、图片和媒体缓存放入对象存储；
4. 为用户、项目、视频和权限建立数据库模型；
5. 为分析任务增加取消、重试、超时和幂等键；
6. 接入结构化日志、指标、错误追踪和磁盘清理策略；
7. 把 Web 层迁移到 FastAPI/Uvicorn 或其他正式 ASGI/WSGI 服务；
8. 对外开放前增加登录、CSRF、限流、审计和数据保留策略。

## 7. 发布检查清单

- [ ] CI 在 Python 3.10/3.11/3.12 通过
- [ ] \`.env\` 和 Cookie 未入 Git
- [ ] Docker healthcheck 正常
- [ ] HTTPS 和访问控制已启用
- [ ] \`BV/av/完整链接\` 均可解析
- [ ] CC、ASR、无字幕降级路径各测试一次
- [ ] LLM 总结和时间戳问答各测试一次
- [ ] Markdown 和一图流下载各测试一次
- [ ] 备份当前 Git tag、\`.env\` 密钥和 \`/data\` 缓存策略
- [ ] 已准备上一版本回滚命令

## 8. 应用层 Token

公网部署时可在 `.env` 中设置 `WEB_AUTH_TOKEN`。除健康检查和封面读取外，Web API 需要携带：

```bash
curl -H "Authorization: Bearer <WEB_AUTH_TOKEN>" http://127.0.0.1:8765/api/settings
```

Token 只作为应用层额外保护，仍应同时启用 HTTPS、Caddy/Nginx Basic Auth 或公司 SSO。

SQLite 文件默认位于 `.bili-agent/bili-agent.sqlite3`。生产环境请把 `.bili-agent` 纳入备份范围，并保持它与 `.env` 一样不提交 Git。
