from __future__ import annotations

from datetime import timezone

from .llm import format_timestamp
from .models import AnalysisResult


def _published(value) -> str:
    if value is None:
        return "未知"
    return value.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def _page_offsets(result: AnalysisResult) -> dict[int, float]:
    offset = 0.0
    offsets: dict[int, float] = {}
    for page in sorted(result.metadata.pages, key=lambda item: item.page_index):
        offsets[page.page_index] = offset
        offset += max(float(page.duration_seconds), 0.0)
    return offsets


def _range(start: float, end: float) -> str:
    begin = format_timestamp(start)
    finish = format_timestamp(end)
    return begin if begin == finish else f"{begin}-{finish}"


def render_markdown(result: AnalysisResult) -> str:
    metadata = result.metadata
    summary = result.summary
    offsets = _page_offsets(result)
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
            offset = offsets.get(item.page_index, 0.0)
            global_start = item.global_start if item.global_start is not None else offset + item.start
            global_end = item.global_end if item.global_end is not None else offset + item.end
            location = f"P{item.page_index + 1} {_range(global_start, global_end)}"
            if global_start != item.start:
                location += f"（本P {_range(item.start, item.end)}）"
            lines.append(
                f"- **{location}｜{item.source_label}{confidence}**：{item.content}"
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
                global_start = offsets.get(item.page.page_index, 0.0) + segment.start
                prefix = f"`{format_timestamp(global_start)}`"
                if global_start != segment.start:
                    prefix += f"（P{item.page.page_index + 1} 本P `{format_timestamp(segment.start)}`）"
                lines.append(f"{prefix} {segment.text}")
        else:
            lines.append(f"> {item.transcript.notice or '无字幕。'}")
        lines.append("")
    if result.degraded:
        lines.extend(["> 提示：未配置 LLM_API_KEY，本文档中的总结为原文截断降级内容。", ""])
    return "\n".join(lines).rstrip() + "\n"
