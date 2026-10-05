# Security Policy

## Reporting a vulnerability

请不要在公开 Issue 中发布 API Key、B 站 Cookie、服务器地址或可复现的敏感数据。

如果仓库已启用 GitHub Security Advisories，请通过 Private vulnerability reporting 提交；否则请先联系仓库维护者，再提供最小化复现步骤。

## 本地部署建议

- 不要提交 `.env`，使用 `.env.example` 作为配置模板。
- 公网部署时设置 `WEB_AUTH_TOKEN`，并通过 HTTPS 反向代理访问。
- 不要把 8765 端口直接暴露到公网。
- 定期清理 `.bili-agent` 中的音频、视频和 SQLite 会话数据。
- 遵守 Bilibili 的服务条款、版权规则和访问频率限制。
