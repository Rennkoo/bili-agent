from __future__ import annotations

import argparse
import asyncio
import json
import logging
from pathlib import Path

from . import __version__
from .agent import BiliAgent
from .config import Settings
from .markdown import render_markdown


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="bili-agent", description="分析 Bilibili 视频并生成带时间戳的 Markdown 笔记。")
    parser.add_argument("--version", action="version", version=f"bili-agent {__version__}")
    parser.add_argument("--env-file", type=Path, default=None, help="可选 .env 文件路径。")
    parser.add_argument("--verbose", action="store_true", help="输出调试日志。")
    sub = parser.add_subparsers(dest="command", required=True)

    analyze = sub.add_parser("analyze", help="获取视频、总结并导出 Markdown。")
    analyze.add_argument("video", help="视频链接、BV 号或 av 号。")
    analyze.add_argument("--output", "-o", type=Path, help="Markdown 输出路径。")
    analyze.add_argument("--json", action="store_true", help="同时打印结构化 JSON。")
    analyze.add_argument("--json-output", type=Path, help="将完整分析结果保存为 JSON 文件。")
    analyze.add_argument("--enable-asr", action="store_true", help="无 CC 字幕时启用 yt-dlp + faster-whisper。")
    analyze.add_argument("--enable-multimodal", action="store_true", help="下载低清视频并启用关键帧/OCR/视觉分析。")

    ask = sub.add_parser("ask", help="分析视频并针对字幕内容提问。")
    ask.add_argument("video", help="视频链接、BV 号或 av 号。")
    ask.add_argument("question", help="要询问的问题。")
    ask.add_argument("--top-k", type=int, default=5, help="检索字幕片段数量。")
    ask.add_argument("--enable-asr", action="store_true", help="无 CC 字幕时启用 ASR。")
    ask.add_argument("--enable-multimodal", action="store_true", help="启用关键帧/OCR/视觉分析。")

    web = sub.add_parser("web", help="启动浏览器聊天面板。")
    web.add_argument("--host", default="127.0.0.1", help="监听地址，默认 127.0.0.1。")
    web.add_argument("--port", type=int, default=8765, help="监听端口，默认 8765。")
    web.add_argument("--open", action="store_true", help="启动后自动打开浏览器。")
    return parser


async def _run(args: argparse.Namespace) -> int:
    settings = Settings.from_env(args.env_file)
    agent = BiliAgent(settings)
    enable_asr = True if getattr(args, "enable_asr", False) else None
    enable_multimodal = True if getattr(args, "enable_multimodal", False) else None
    if args.command == "web":
        from .web import serve

        serve(settings, args.host, args.port, open_browser=args.open)
        return 0

    result = await agent.analyze(args.video, enable_asr=enable_asr, enable_multimodal=enable_multimodal)
    if args.command == "analyze":
        markdown = render_markdown(result)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(markdown, encoding="utf-8")
            print(f"Markdown 已保存到: {args.output.resolve()}")
        else:
            print(markdown)
        if args.json:
            print(json.dumps(result.summary.model_dump(mode="json"), ensure_ascii=False, indent=2))
        if args.json_output:
            args.json_output.parent.mkdir(parents=True, exist_ok=True)
            args.json_output.write_text(
                json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            print(f"JSON 已保存到: {args.json_output.resolve()}")
        return 0

    answer = await agent.ask(result, args.question, top_k=max(args.top_k, 1))
    print(answer.answer)
    return 0


def main() -> None:
    args = _parser().parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        raise SystemExit(asyncio.run(_run(args)))
    except KeyboardInterrupt:
        raise SystemExit(130)
    except Exception as exc:
        logging.getLogger(__name__).error("执行失败: %s", exc)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
