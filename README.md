# bili-agent

一个可直接运行的 B 站视频分析 Agent：输入 BV 号、av 号或视频链接，获取元数据、CC 字幕、ASR 和可选的关键帧/OCR/视觉证据，按分 P 总结，导出 Markdown 笔记，并支持基于统一时间线的问答。

## 安装

要求 Python 3.10+。建议使用虚拟环境：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e .
Copy-Item .env.example .env
```

将 `.env` 中的 `LLM_API_KEY`、`LLM_BASE_URL` 和 `LLM_MODEL` 配置为 OpenAI 或其他 OpenAI 兼容服务。没有 `LLM_API_KEY` 时仍可运行，程序会使用字幕原文截断生成降级总结，并明确提示。

部分 B 站字幕需要登录态，可在 `.env` 中填写 `BILI_SESSDATA`、`BILI_BILI_JCT` 和 `BILI_BUVID3`。请勿把包含真实 Cookie 的 `.env` 提交到版本库。

## 使用

```powershell
# 生成 Markdown 笔记，同时打印结构化 JSON
bili-agent analyze "https://www.bilibili.com/video/BV1xx411c7mD" --output notes.md --json

# 同时保存完整分析结果 JSON
bili-agent analyze "BV1xx411c7mD" --output notes.md --json-output analysis.json

# av 号和 BV 号同样支持
bili-agent analyze av170001 --output notes.md
bili-agent analyze BV1xx411c7mD --output notes.md

# 无字幕或图文视频：下载低清视频，提取关键帧并启用 OCR/视觉分析
bili-agent analyze "BV1xx411c7mD" --enable-multimodal --output notes.md

# 基于字幕检索相关片段，再让 LLM 回答；回答会带来源时间戳
bili-agent ask "BV1xx411c7mD" "视频中提到的主要方法是什么？"

# 启动聊天面板
bili-agent web --open
```

打开面板后，点击右上角齿轮按钮即可填写 `LLM_API_KEY`、`LLM_BASE_URL` 和 `LLM_MODEL`。保存后配置立即生效，并写入本机项目的 `.env` 文件；已有 API Key 只显示掩码，不会回显明文。

默认不下载音频。如果某个分 P 没有 CC 字幕，可显式启用预留的 faster-whisper 路径：

```powershell
python -m pip install -e ".[asr]"
bili-agent analyze "BV1xx411c7mD" --enable-asr --output notes.md
```

启用 ASR 后，程序通过 `yt-dlp` 下载对应分 P 的最佳音频，再交给 faster-whisper 转写。音频保存在 `ASR_CACHE_DIR`，需要本机可用的 ffmpeg。

## 项目结构

```text
src/bili_agent/
  agent.py             # 编排分析与问答流程
  asr.py               # ASR 协议、yt-dlp 音频下载、faster-whisper 实现
  bilibili_client.py   # bilibili-api-python 元数据/分P适配与 CC 字幕
  cli.py               # 命令行入口
  config.py            # .env 配置
  markdown.py          # Markdown 导出
  models.py            # Pydantic 数据模型
  parser.py            # BV/av/URL 解析
  retrieval.py         # 字幕关键词检索
  llm.py               # OpenAI 兼容接口与无 Key 降级
  multimodal.py        # 低清视频、关键帧、OCR 和视觉证据提取
```

## 说明

`bilibili-api-python` 是异步库，负责视频信息和分 P 获取；CC 字幕的实际内容来自播放器字幕数据，适配层会优先尝试库中可用的播放器 helper，再使用同一视频凭据访问播放器接口。B 站接口和字幕权限可能随时间变化，程序会将单个分 P 的失败记录为日志并继续处理其他分 P。

更完整的安装、命令参数、配置项和故障排查请参阅 [docs/USAGE.md](docs/USAGE.md)。

上线、Docker、HTTPS、Git 分支和回滚方案请参阅 [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md)。
