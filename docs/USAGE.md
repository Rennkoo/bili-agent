# bili-agent 使用说明

## 1. 安装

要求 Python 3.10 或更高版本。

```powershell
cd C:\Users\86199\Documents\Codex\2026-09-14\new-chat-4\outputs\bili-agent
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e .
```

如果 PowerShell 禁止激活虚拟环境，也可以直接调用：

```powershell
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\python.exe -m bili_agent --version
```

复制配置模板：

```powershell
Copy-Item .env.example .env
```

## 2. LLM 配置

编辑 `.env`：

```dotenv
LLM_API_KEY=your-api-key
LLM_BASE_URL=https://api.openai.com/v1
LLM_MODEL=gpt-4o-mini
LLM_TIMEOUT_SECONDS=90
MAX_TRANSCRIPT_CHARS=24000
MAX_CONCURRENT_ANALYSES=2
MAX_QUESTION_CHARS=4000
MAX_VIDEO_INPUT_CHARS=500
MAX_COVER_BYTES=5242880
MULTIMODAL_ENABLED=false
MULTIMODAL_MAX_FRAMES=6
MEDIA_CACHE_DIR=.bili-agent/media
# 可选：视觉模型名，默认使用 LLM_MODEL
VISION_MODEL=
```

兼容 OpenAI Chat Completions 的服务通常只需要替换 `LLM_BASE_URL` 和 `LLM_MODEL`。

`LLM_TIMEOUT_SECONDS` 控制单次 LLM 请求超时；`MAX_TRANSCRIPT_CHARS` 控制发送给 LLM 的字幕最大字符数。

`MAX_CONCURRENT_ANALYSES` 限制同时运行的分析任务数；`MAX_QUESTION_CHARS` 和 `MAX_VIDEO_INPUT_CHARS` 限制问答与视频输入长度；`MAX_COVER_BYTES` 限制封面代理允许下载的最大字节数。

如果不配置 `LLM_API_KEY`，程序仍然可以获取视频和字幕，但总结会使用字幕原文截断作为降级内容，问答会返回检索到的字幕片段和时间戳。

## 3. B 站登录态

公开字幕不需要登录。若某些视频字幕接口返回空数组，可尝试配置浏览器 Cookie：

```dotenv
BILI_SESSDATA=...
BILI_BILI_JCT=...
BILI_BUVID3=...
```

Cookie 属于敏感凭证，不要提交到 Git，也不要复制给他人。`.env` 已被 `.gitignore` 忽略。

## 4. 生成 Markdown 笔记

输入可以是完整链接、BV 号或 av 号：

```powershell
bili-agent analyze "https://www.bilibili.com/video/BVxxxx"
bili-agent analyze "BVxxxx"
bili-agent analyze "av170001"
```

保存 Markdown：

```powershell
bili-agent analyze "BVxxxx" --output notes.md
```

保存结构化 JSON：

```powershell
bili-agent analyze "BVxxxx" --json-output analysis.json
```

同时在终端打印 JSON：

```powershell
bili-agent analyze "BVxxxx" --json
```

全局参数必须放在子命令前：

```powershell
bili-agent --env-file .env analyze "BVxxxx" --output notes.md
```

## 5. 视频问答

## 5.1 聊天面板

启动本地聊天面板：

```powershell
bili-agent web --open
```

不自动打开浏览器时：

```powershell
bili-agent web --host 127.0.0.1 --port 8765
```

然后访问 `http://127.0.0.1:8765`。面板支持新建会话、视频分析、连续提问、来源时间戳、章节查看和 Markdown 下载。按 `Ctrl+C` 停止服务。

右上角点击齿轮按钮“配置 LLM API”，可以直接填写 API Key、Base URL 和模型名。保存后立即对新的分析和问答生效，并同步保存到项目 `.env`。API Key 输入框不会回显已有密钥；留空表示保持原 Key，勾选“清除当前 API Key”才会切换回降级模式。建议只在本机面板中使用，不要把 `.env` 提交到版本库。

问答命令会先对字幕片段做关键词检索，再使用相关片段调用 LLM：

```powershell
bili-agent ask "BVxxxx" "视频中提到的主要方法是什么？"
```

调整检索片段数量：

```powershell
bili-agent ask "BVxxxx" "什么是缓存？" --top-k 8
```

回答会包含来源分 P 和时间戳。

## 6. 无字幕视频与 ASR

默认不下载音频，也不启用 ASR。需要 ASR 时安装可选依赖：

```powershell
python -m pip install -e ".[asr]"
```

本机还需要 `ffmpeg`。运行：

```powershell
bili-agent analyze "BVxxxx" --enable-asr --output notes.md
```

处理流程是：优先请求 CC 字幕；无字幕时用 `yt-dlp` 下载对应分 P 音频，再用 faster-whisper 转写，最后纳入总结、Markdown 和问答检索。

## 7. 多模态视频

对于没有 CC、没有清晰语音、主要依靠 PPT、字幕卡或代码画面表达的视频，可以额外启用关键帧通道：

```powershell
python -m pip install -e ".[vision]"
bili-agent analyze "BVxxxx" --enable-multimodal --output notes.md
```

该通道会通过 `yt-dlp` 下载低清视频，使用 PyAV 按时间均匀抽取关键帧；如果安装了本地 `pytesseract` 和对应的 Tesseract 语言包，会写入 OCR 证据；配置 `LLM_API_KEY` 后，还会把关键帧发送到 OpenAI 兼容视觉模型，生成画面、图表、代码和可见文字描述。所有结果都会进入统一时间线，供总结、Markdown 和问答复用。聊天面板中也可以勾选“分析关键帧、画面文字和视觉内容”。

没有视觉依赖、视觉模型或有效 Key 时，程序会保留已有 CC/ASR 结果并记录降级日志，不会因为单个分 P 的画面分析失败而中止整段任务。

## 8. 常见问题

### `ModuleNotFoundError`

确认当前使用的是安装项目依赖的 Python：

```powershell
python -m pip install -e .
python -m bili_agent --version
```

### 获取不到视频信息

可能是链接无效、网络访问受限、B 站风控或视频需要登录。可以配置 B 站 Cookie，并使用 `--verbose` 查看日志：

```powershell
bili-agent --verbose analyze "BVxxxx" --output notes.md
```

### 没有字幕

B 站并非每个视频都提供公开 CC 字幕。默认程序会在 Markdown 中标注无字幕；需要转写时使用 ASR 选项。

### LLM 返回格式错误

程序会先尝试 JSON response format；如果服务不支持，则自动回退到普通文本并提取 JSON。仍失败时会使用原文降级总结。

## 9. 开发检查

```powershell
python -m compileall -q src tests
python -m pytest -q
```

测试不包含真实 B 站网络请求和真实 LLM 调用。

JSON 输出包含视频元数据、每个分 P 的字幕和分 P 总结，适合后续接入其他知识库或笔记系统。

## 10. 退出码

成功返回 `0`；参数错误、网络/API 错误或依赖缺失返回 `1`；用户按 Ctrl+C 中断返回 `130`。
