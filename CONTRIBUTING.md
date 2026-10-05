# Contributing to bili-agent

感谢参与 bili-agent。请先阅读 `README.md`、`docs/USAGE.md` 和 `docs/DEPLOYMENT.md`，再开始修改。

## 开发环境

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
```

如果需要验证 ASR 或视觉链路，再安装对应可选依赖，并确保本机有 `ffmpeg`。

## 提交前检查

```powershell
python -m pytest -q
python -m compileall -q src tests
node --check src/bili_agent/static/app.js
git diff --check
```

请为行为变化补充测试，不要提交 `.env`、Cookie、API Key、音频缓存、模型缓存或生成的笔记文件。

## Pull Request

- 说明问题、实现方式和验证结果。
- 保持改动聚焦，避免把格式化或无关重构混入功能提交。
- 涉及 Web 界面时附上截图或说明验证过的视口。
- 涉及 B 站真实请求时不要在日志、测试输出或 PR 中暴露凭据。
