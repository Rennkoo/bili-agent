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

将 `.env` 中的 `LLM_API_KEY`、`LLM_BASE_URL` 和 `LLM_MODEL` 配置为 OpenAI 或其他 OpenAI 兼容服务。没有 `LLM_API_KEY` 时仍可运行，程序会使用已有字幕、ASR 或视觉证据截断生成降级总结，并明确提示。

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

面板会先读取视频信息和分P列表，再让你选择需要分析的分P；长视频默认不勾选全部分P，确认选择后才开始字幕、ASR、视觉和总结流程。

打开面板后，点击右上角齿轮按钮即可填写 `LLM_API_KEY`、`LLM_BASE_URL` 和 `LLM_MODEL`。保存后配置立即生效，并写入本机项目的 `.env` 文件；已有 API Key 只显示掩码，不会回显明文。

默认开启无字幕时的音频转写。如果某个分 P 没有 CC 字幕，程序才会加载 faster-whisper 并进行转写；可在面板中取消勾选，或设置 `ASR_ENABLED=false`：

```powershell
python -m pip install -e ".[asr]"
bili-agent analyze "BV1xx411c7mD" --enable-asr --output notes.md
```

启用 ASR 后，程序通过 `yt-dlp` 下载适合转写的音频，再交给 faster-whisper 转写。默认使用速度更快的 `base` 模型、`int8`、单束搜索和低码率音频；想提高识别质量可将 `ASR_MODEL=small`，并适当增大 `ASR_BEAM_SIZE`。音频保存在 `ASR_CACHE_DIR`，需要本机可用的 ffmpeg。

ASR 默认使用 `ASR_LANGUAGE=auto` 自动检测语种，支持中文、日语、英语等 faster-whisper 多语言模型覆盖的语言。若音频较短、混合语言较多或自动检测不稳定，可在 `.env` 中固定语言，例如 `ASR_LANGUAGE=ja`（日语）、`ASR_LANGUAGE=en`（英语）或 `ASR_LANGUAGE=zh`（中文）。不要使用 `.en` 结尾的英语专用 Whisper 模型，否则无法识别日语和中文。

如果需要“多语言候选后再选择”，可配置 `ASR_CANDIDATE_LANGUAGES=zh,ja,en`。程序会保留同一时间片的候选文本；`ASR_RERANK_MODE=confidence` 使用转写置信度选择，`ASR_RERANK_MODE=llm` 则将候选和分P上下文交给 LLM 做页面级选择。候选模式会增加转写时间，默认关闭以保持速度。

如果本机有 NVIDIA CUDA 环境，建议设置 `ASR_DEVICE=cuda`、`ASR_COMPUTE_TYPE=float16`；没有 CUDA 时使用 `ASR_DEVICE=cpu`、`ASR_COMPUTE_TYPE=int8`。

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

聊天问答内置元数据查询、整体总结、章节时间线、知识点提取、字幕检索和证据问答技能；短追问会自动结合当前会话上下文进行检索。

分析完成后，右侧“转写对照”会显示每条 CC/ASR 文本的时间戳和播放按钮。首次点击某个分P的播放按钮时，程序通过 `yt-dlp` 懒下载并缓存该分P音频，随后从对应时间点播放，支持暂停、拖动和反复对照；音频接口只允许访问当前分析会话中已选的分P。

Web 面板可通过 `WEB_AUTH_TOKEN` 开启 Bearer Token 保护；本地默认留空，公网部署时请同时配置 HTTPS 和反向代理认证。

长视频会并发获取各分P CC 字幕，并在面板中显示 `P x / 总P数` 进度。`CAPTION_TIMEOUT_SECONDS` 控制单个分P字幕请求超时，`MAX_CONCURRENT_CAPTION_FETCHES` 控制字幕并发数。启用 ASR 后会显示音频下载和转写进度，`ASR_TIMEOUT_SECONDS` 控制单个分P的 ASR 总超时时间。
