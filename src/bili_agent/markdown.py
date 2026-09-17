from __future__ import annotations

from datetime import timezone

from .llm import format_timestamp
from .models import AnalysisResult


def _published(value) -> str:
    if value is None:
        return "未知"
    return value.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def render_markdown(result: AnalysisResult) -> str:
    metadata = result.metadata
    summary = result.summary
    lines = [
        f"# {metadata.title}",
        "",
        f"- **原链接**：[{metadata.url}]({metadata.url})",
        f"- **Up主**：{metadata.author}",
        f"- **发布时间**：{_published(metadata.pubdate)}",
        f"- **总时长**：{format_timestamp(metadata.duration_seconds)}",
        f"- **分P数**：{len(metadata.pages)}",
        "",
        "## 整体概述",
        "",
        summary.overall_summary,
        "",
        "## 章节时间线",
        "",
    ]
    if summary.chapters:
        for chapter in summary.chapters:
            lines.append(f"- **{chapter.timestamp}｜{chapter.title}**：{chapter.summary}")
            for point in chapter.key_points:
                lines.append(f"  - {point}")
    else:
        lines.append("暂无章节信息。")
    lines.extend(["", "## 核心知识点", ""])
    if summary.knowledge_points:
        for point in summary.knowledge_points:
            lines.append(f"- **{point.term}**：{point.explanation}")
    else:
        lines.append("暂无知识点信息。")

    lines.extend(["", "## 多模态证据时间线", ""])
    if result.timeline:
        for item in result.timeline:
            confidence = f"，置信度 {item.confidence:.0%}" if item.confidence < 1 else ""
            lines.append(
                f"- **P{item.page_index + 1} {format_timestamp(item.start)}｜{item.source_label}{confidence}**：{item.content}"
            )
    else:
        lines.append("暂无 OCR、视觉或其他补充证据。")

    lines.extend(["", "## 分P总结", ""])
    for item in result.pages:
        lines.extend([f"### P{item.page.page_index + 1}：{item.page.title}", "", item.summary.overall_summary, ""])
        if item.summary.chapters:
            for chapter in item.summary.chapters:
                lines.append(f"- **{chapter.timestamp}｜{chapter.title}**：{chapter.summary}")
        lines.append("")

    lines.extend(["## 完整字幕文本", ""])
    for item in result.pages:
        lines.extend([f"### P{item.page.page_index + 1}：{item.page.title}", ""])
        if item.transcript.segments:
            for segment in item.transcript.segments:
                lines.append(f"`{format_timestamp(segment.start)}` {segment.text}")
        else:
            lines.append(f"> {item.transcript.notice or '无字幕。'}")
        lines.append("")
    if result.degraded:
        lines.extend(["> 提示：未配置 LLM_API_KEY，本文档中的总结为原文截断降级内容。", ""])
    return "\n".join(lines).rstrip() + "\n"
