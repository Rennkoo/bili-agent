# bili-agent 演示文档

本文用于现场演示、录屏和上线验收。默认以 Windows PowerShell 为例，Linux/macOS 只需替换虚拟环境激活命令。

## 1. 演示目标

用一个 B 站视频完成下面的闭环：

```text
输入链接 -> 读取元数据 -> 选择分P -> 获取 CC/ASR/视觉证据
       -> 生成章节和知识点 -> 基于时间线追问 -> 导出 Markdown/JSON
```

推荐演示顺序：先用短视频验证链路，再演示无字幕或图文视频，最后演示长视频的分P选择、进度和取消。

## 2. 启动前准备

在仓库根目录执行：

```powershell
cd C:\Users\86199\Documents\Codex\2026-09-14\new-chat-4\outputs\bili-agent
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
Copy-Item .env.example .env
```

如果 PowerShell 不允许激活虚拟环境，可以直接使用：

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

编辑 `.env`，最小配置如下：

```dotenv
LLM_API_KEY=填写你的兼容 OpenAI API Key
LLM_BASE_URL=https://api.openai.com/v1
LLM_MODEL=gpt-4o-mini
```

如果使用其他兼容服务，只替换 `LLM_BASE_URL` 和 `LLM_MODEL`。不要把真实 Key、B 站 Cookie 或分析结果提交到 Git。

没有 LLM Key 也可以演示数据获取、字幕时间线、降级总结和证据问答，但总结会显示“降级内容”，不能代表模型总结效果。

## 3. 环境自检

演示前先执行：

```powershell
python -m bili_agent doctor
```

示例输出：

```text
bili-agent 0.1.0 运行诊断
Python: 3.11.x
LLM: 已配置
存储: 可写
ASR: 不可用
yt-dlp: 可用
ffmpeg: 不可用
视觉依赖: 不可用
提示:
- ASR 已开启但未安装 faster-whisper。
- ASR 需要 ffmpeg，但当前 PATH 中未找到。
```

缺少 ASR/视觉依赖不会阻止有 CC 字幕的视频演示。需要机器转写时安装：

```powershell
python -m pip install -e ".[asr]"
```

同时确保系统 PATH 中存在 `ffmpeg`。图文视频需要视觉扩展：

```powershell
python -m pip install -e ".[vision]"
```

也可以获得机器可读诊断：

```powershell
python -m bili_agent doctor --json
```

## 4. 演示 A：聊天面板完整流程

### 4.1 启动面板

```powershell
python -m bili_agent web --host 127.0.0.1 --port 8765
```

浏览器打开：

```text
http://127.0.0.1:8765/
```

另开一个终端检查服务：

```powershell
Invoke-RestMethod http://127.0.0.1:8765/api/health | ConvertTo-Json -Depth 5
```

重点观察：

- `ready` 为 `true`；
- `llm_configured` 反映是否填写 Key；
- `capabilities` 展示 ASR、yt-dlp、ffmpeg 和视觉能力；
- `warnings` 给出可解释的降级原因；
- 接口不会返回 API Key 明文。

### 4.2 输入视频并选择分P

在面板中粘贴以下任一种输入：

```text
https://www.bilibili.com/video/BVxxxx
BVxxxx
av170001
```

点击“分析链接”。面板先只获取标题、封面、UP 主、时长和分P列表，不会立即处理全部内容。演示时说明：

1. 短视频可以全选；
2. 长课程只选择前 1～2 个分P；
3. 分P选择完成后再点击开始分析，避免误触发大量音频下载和模型请求。

### 4.3 观察分析过程

面板会按阶段显示进度：

```text
获取视频信息 -> 获取 CC 字幕 -> 无字幕时音频转写
             -> 关键帧/OCR/视觉 -> 生成章节 -> 完成
```

正常有字幕视频的日志通常类似：

```text
INFO  正在获取视频信息和分P列表
INFO  正在获取 CC 字幕（1/2）
INFO  正在获取 CC 字幕（2/2）
INFO  字幕/语音内容已整理，正在生成时间线
INFO  正在总结第 1 P
INFO  正在总结第 2 P
INFO  分析完成
```

无字幕时会转入：

```text
INFO  第 1 P 没有 CC 字幕，准备下载音频
INFO  正在转写第 1 P 的音频
INFO  ASR 已处理约 120 秒音频
INFO  语音内容已整理，正在生成时间线
```

看到 ASR 进度在几十秒内没有变化，不代表一定卡死。长音频转写会按心跳更新；如果超过配置的 `ASR_TIMEOUT_SECONDS`，该分P会保留降级状态并继续其他分P。

### 4.4 查看结果和追问

分析完成后依次展示：

- 视频标题、UP 主、发布时间、总时长和封面；
- 整体概述；
- 章节时间线；
- 核心知识点；
- 每个分P的内容来源和完整字幕/ASR；
- “转写对照”中的时间戳和音频播放按钮；
- 降级提示或局部失败提示。

推荐问题：

```text
这个视频主要解决了什么问题？
按时间线概括第一个分P。
视频中提到了哪些方法？
第二个分P和第一个分P有什么区别？
你刚才的结论来自哪一段内容？
```

回答应带有类似来源：

```text
来源：P1 00:03:12-00:03:28
```

点击来源或对应播放按钮，核对回答是否真的来自原文证据。这是本 Agent 与只生成摘要的工具的主要区别。

### 4.5 导出结果

面板中点击 Markdown 下载即可保存笔记。命令行也可以导出：

```powershell
python -m bili_agent analyze "BVxxxx" `
  --output .\outputs\demo-note.md `
  --json-output .\outputs\demo-result.json
```

Markdown 至少包含：标题、原链接、UP 主、发布时间、时长、整体概述、章节、知识点、完整字幕和多模态证据时间线。

## 5. 演示 B：无字幕视频

无字幕视频的处理优先级是：

```text
CC 字幕 -> yt-dlp 音频 -> faster-whisper ASR -> LLM 总结/问答
```

运行前确认：

```powershell
python -m bili_agent doctor
ffmpeg -version
```

命令行测试：

```powershell
python -m bili_agent analyze "BVxxxx" --enable-asr `
  --output .\outputs\demo-asr-note.md
```

语言配置：

```dotenv
ASR_LANGUAGE=auto
# 混合语言识别不稳定时可以固定：zh、ja 或 en
# ASR_LANGUAGE=ja
ASR_MODEL=base
ASR_DEVICE=cpu
ASR_COMPUTE_TYPE=int8
```

如果需要比较中文、日语和英语候选：

```dotenv
ASR_CANDIDATE_LANGUAGES=zh,ja,en
ASR_RERANK_MODE=confidence
```

候选模式会重复解码，适合质量验证，不适合 1 GiB 内存服务器的日常全量运行。

## 6. 演示 C：图文或无明显语音视频

对于 PPT、代码演示、字幕卡、图表等内容，启用关键帧和 OCR：

```powershell
python -m bili_agent analyze "BVxxxx" `
  --enable-multimodal `
  --output .\outputs\demo-visual-note.md
```

面板会把 CC、ASR、OCR、视觉描述和元数据统一放进时间线。问答时可以直接询问：

```text
画面中出现了哪些步骤？
这张图表表达的趋势是什么？
视频中代码示例的关键配置是什么？
```

视觉模型未配置时，仍保留可用的 OCR/时间线证据，不会因为单个视觉阶段失败而中止整段分析。

## 7. CLI 快速演示脚本

### 分析并导出

```powershell
python -m bili_agent analyze "https://www.bilibili.com/video/BVxxxx" `
  --output .\outputs\note.md `
  --json `
  --json-output .\outputs\result.json
```

### 直接问答

```powershell
python -m bili_agent ask "BVxxxx" "视频中提到的核心方法是什么？" --top-k 5
```

### 调试日志

```powershell
python -m bili_agent --verbose analyze "BVxxxx" --output .\outputs\debug-note.md
```

### 停止服务

```text
Ctrl+C
```

## 8. 演示验收清单

| 场景 | 验收点 | 预期结果 |
| --- | --- | --- |
| 有 CC 字幕 | 获取与总结 | 保留时间戳，生成章节和知识点 |
| 多分P | 选择范围 | 只分析勾选分P，并正确合并时间线 |
| 无字幕有语音 | ASR | 下载音频、转写并进入问答索引 |
| 日语/英语 | 语言识别 | `auto` 或指定语言可生成对应文本 |
| 图文视频 | OCR/视觉 | 画面证据进入时间线和 Markdown |
| 无 LLM Key | 降级 | 服务不崩溃，明确标注降级内容 |
| 长视频 | 进度/取消 | 可看到阶段进度，可取消并释放任务 |
| 问答 | 来源核验 | 回答带 P 编号和时间戳 |
| 导出 | Markdown/JSON | 文件可打开，包含完整证据 |
| 重启服务 | 状态恢复 | SQLite 会话和任务记录可恢复 |

## 9. 常见故障

### 面板打开但显示空白

先检查静态资源：

```powershell
Invoke-WebRequest http://127.0.0.1:8765/ -UseBasicParsing | Select-Object StatusCode
Invoke-WebRequest http://127.0.0.1:8765/app.js -UseBasicParsing | Select-Object StatusCode
```

两者都应为 `200`。如果服务是从虚拟环境运行，确认使用的是同一个 Python 安装出的 `bili-agent`。

### 一直停在获取字幕

降低单P超时并减少并发：

```dotenv
CAPTION_TIMEOUT_SECONDS=20
MAX_CONCURRENT_CAPTION_FETCHES=3
```

使用 `--verbose` 查看具体分P；单个分P失败不会阻塞其他分P。

### ASR 太慢或占满内存

1. 先只选择一个分P；
2. 使用 `ASR_MODEL=base`、`ASR_COMPUTE_TYPE=int8`；
3. 关闭多语言候选；
4. 服务器只有 1 GiB 内存时关闭 `ASR_ENABLED`，优先使用 CC 字幕；
5. 需要稳定长视频转写时升级到至少 4 vCPU、8 GiB 内存。

### 没有 API Key

这是可运行的降级模式，不是启动故障。使用 `doctor` 或 `/api/health` 确认：

```text
LLM: 未配置（使用降级模式）
```

## 10. 演示结论模板

演示结束时可以用下面这段话总结：

> bili-agent 已完成从 B 站视频输入、分P选择、CC/ASR/视觉内容获取，到结构化总结、时间线问答和 Markdown/JSON 导出的完整闭环。它不仅给出摘要，还能保留原始证据和时间戳，用户可以通过播放对应片段核验回答。没有 LLM、字幕或单个分P失败时，系统会降级并继续运行，而不是直接崩溃。

