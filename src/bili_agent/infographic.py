from __future__ import annotations

import html
from collections import Counter

from .llm import format_timestamp
from .models import AnalysisResult


WIDTH = 1600


def _escape(value: object) -> str:
    return html.escape(str(value or ""), quote=True)


def _wrap(text: str, width: int) -> list[str]:
    compact = " ".join(str(text or "").split())
    if not compact:
        return ["暂无内容"]
    return [compact[index : index + width] for index in range(0, len(compact), width)]


def _text_lines(
    lines: list[str],
    x: int,
    y: int,
    *,
    size: int,
    fill: str,
    line_height: int,
    weight: int = 400,
) -> str:
    return "".join(
        f'<text x="{x}" y="{y + index * line_height}" font-size="{size}" '
        f'font-weight="{weight}" fill="{fill}">{_escape(line)}</text>'
        for index, line in enumerate(lines)
    )


def _section_label(title: str, y: int, accent: str = "#c9f26d") -> str:
    return (
        f'<rect x="84" y="{y - 24}" width="8" height="28" rx="4" fill="{accent}"/>'
        f'<text x="112" y="{y}" font-size="24" font-weight="750" fill="#eef4e9">{_escape(title)}</text>'
        f'<line x1="350" y1="{y - 8}" x2="1516" y2="{y - 8}" stroke="#2b372d" stroke-width="2"/>'
    )


def render_infographic(result: AnalysisResult) -> str:
    """Render a self-contained, editable SVG one-page brief from an analysis result."""
    metadata = result.metadata
    summary = result.summary
    chapters = summary.chapters[:8]
    knowledge = summary.knowledge_points[:8]
    evidence = result.timeline[:10]
    modality_counts = Counter(item.modality for item in result.timeline)

    title_lines = _wrap(metadata.title, 21)[:3]
    summary_lines = _wrap(summary.overall_summary, 43)[:7]
    title_height = max(1, len(title_lines)) * 68
    summary_y = 260 + title_height
    summary_height = max(220, len(summary_lines) * 42 + 92)
    timeline_y = summary_y + summary_height + 72
    timeline_row_height = 104
    timeline_height = max(164, len(chapters) * timeline_row_height + 56)
    knowledge_y = timeline_y + timeline_height + 72
    knowledge_rows = max(1, (len(knowledge) + 1) // 2)
    knowledge_height = max(170, knowledge_rows * 126 + 56)
    evidence_y = knowledge_y + knowledge_height + 72
    evidence_row_height = 78
    evidence_height = max(150, len(evidence) * evidence_row_height + 56)
    height = evidence_y + evidence_height + 118

    author = _escape(metadata.author)
    published = metadata.pubdate.strftime("%Y-%m-%d") if metadata.pubdate else "未知"
    cover = ""
    if metadata.pic:
        cover = (
            f'<image href="{_escape(metadata.pic)}" x="1160" y="86" width="356" height="200" '
            'preserveAspectRatio="xMidYMid slice" clip-path="url(#cover-clip)"/>'
        )

    chapter_svg: list[str] = []
    for index, chapter in enumerate(chapters):
        row_y = timeline_y + 48 + index * timeline_row_height
        chapter_svg.append(f'<circle cx="128" cy="{row_y - 7}" r="10" fill="#c9f26d"/>')
        if index < len(chapters) - 1:
            chapter_svg.append(
                f'<line x1="128" y1="{row_y + 8}" x2="128" y2="{row_y + timeline_row_height - 18}" '
                'stroke="#435342" stroke-width="3"/>'
            )
        chapter_svg.append(_text_lines([chapter.timestamp], 164, row_y, size=21, fill="#f19b76", line_height=26, weight=700))
        chapter_svg.append(_text_lines(_wrap(chapter.title, 30)[:2], 330, row_y, size=24, fill="#eef4e9", line_height=31, weight=700))
        chapter_svg.append(_text_lines(_wrap(chapter.summary, 61)[:2], 330, row_y + 50, size=18, fill="#9fac9b", line_height=26))
    if not chapter_svg:
        chapter_svg.append(_text_lines(["暂无章节信息"], 128, timeline_y + 58, size=21, fill="#8f9a8d", line_height=28))

    knowledge_svg: list[str] = []
    for index, point in enumerate(knowledge):
        column = index % 2
        row = index // 2
        x = 112 + column * 730
        y = knowledge_y + 48 + row * 126
        knowledge_svg.append(f'<rect x="{x}" y="{y - 28}" width="12" height="12" rx="3" fill="#74d7c8"/>')
        knowledge_svg.append(_text_lines(_wrap(point.term, 24)[:1], x + 28, y - 8, size=22, fill="#eef4e9", line_height=28, weight=700))
        knowledge_svg.append(_text_lines(_wrap(point.explanation, 43)[:2], x + 28, y + 30, size=18, fill="#9fac9b", line_height=26))
    if not knowledge_svg:
        knowledge_svg.append(_text_lines(["暂无知识点信息"], 112, knowledge_y + 60, size=21, fill="#8f9a8d", line_height=28))

    evidence_svg: list[str] = []
    for index, item in enumerate(evidence):
        row_y = evidence_y + 48 + index * evidence_row_height
        modality = {"cc": "CC", "asr": "ASR", "ocr": "OCR", "vision": "视觉", "audio_event": "音频", "metadata": "元数据"}.get(item.modality, item.modality)
        accent = {"CC": "#74d7c8", "ASR": "#c9f26d", "OCR": "#f19b76", "视觉": "#c9f26d"}.get(modality, "#8f9a8d")
        evidence_svg.append(f'<rect x="112" y="{row_y - 22}" width="74" height="30" rx="15" fill="#1d2b22" stroke="{accent}" stroke-width="2"/>')
        evidence_svg.append(_text_lines([modality], 130, row_y, size=16, fill=accent, line_height=20, weight=750))
        evidence_svg.append(_text_lines([format_timestamp(item.start)], 210, row_y, size=18, fill="#f19b76", line_height=24, weight=700))
        evidence_svg.append(_text_lines(_wrap(item.content, 73)[:1], 370, row_y, size=19, fill="#c5d0c1", line_height=26))
    if not evidence_svg:
        evidence_svg.append(_text_lines(["暂无可引用证据"], 112, evidence_y + 60, size=21, fill="#8f9a8d", line_height=28))

    modality_text = " · ".join(f"{key.upper()} {value}" for key, value in modality_counts.items()) or "暂无证据"
    degraded_note = " · 降级总结" if result.degraded else ""
    return f'''<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" width="{WIDTH}" height="{height}" viewBox="0 0 {WIDTH} {height}">
  <defs>
    <clipPath id="cover-clip"><rect x="1160" y="86" width="356" height="200" rx="12"/></clipPath>
  </defs>
  <rect width="{WIDTH}" height="{height}" fill="#101511"/>
  <rect x="0" y="0" width="22" height="{height}" fill="#c9f26d"/>
  <rect x="84" y="68" width="68" height="68" rx="16" fill="#c9f26d"/>
  <text x="108" y="114" font-size="42" font-weight="850" fill="#152014">b</text>
  <text x="180" y="108" font-size="22" font-weight="800" letter-spacing="3" fill="#c9f26d">BILIBILI VIDEO BRIEF</text>
  <text x="180" y="141" font-size="16" fill="#8f9a8d">bili-agent · evidence-first visual note</text>
  <rect x="1152" y="78" width="372" height="216" rx="16" fill="#202d21" stroke="#3b4b3c" stroke-width="2"/>
  {cover}
  <text x="1180" y="326" font-size="16" fill="#8f9a8d">封面 · {_escape(metadata.bvid)}</text>
  {_text_lines(title_lines, 84, 230, size=56, fill="#eef4e9", line_height=68, weight=780)}
  <text x="84" y="{summary_y - 20}" font-size="18" fill="#8f9a8d">UP主 {_escape(metadata.author)}  ·  {published}  ·  {format_timestamp(metadata.duration_seconds)}  ·  {len(metadata.pages)} P</text>
  {_section_label("整体概述", summary_y + 58)}
  <rect x="84" y="{summary_y + 86}" width="1432" height="{summary_height - 26}" rx="14" fill="#182119" stroke="#2b372d" stroke-width="2"/>
  {_text_lines(summary_lines, 116, summary_y + 139, size=25, fill="#dbe6d8", line_height=42)}
  {_section_label("章节时间线", timeline_y + 32, "#f19b76")}
  <rect x="84" y="{timeline_y + 62}" width="1432" height="{timeline_height - 18}" rx="14" fill="#151c17" stroke="#2b372d" stroke-width="2"/>
  {''.join(chapter_svg)}
  {_section_label("核心知识点", knowledge_y + 32, "#74d7c8")}
  <rect x="84" y="{knowledge_y + 62}" width="1432" height="{knowledge_height - 18}" rx="14" fill="#151c17" stroke="#2b372d" stroke-width="2"/>
  {''.join(knowledge_svg)}
  {_section_label("代表性证据", evidence_y + 32)}
  <rect x="84" y="{evidence_y + 62}" width="1432" height="{evidence_height - 18}" rx="14" fill="#151c17" stroke="#2b372d" stroke-width="2"/>
  {''.join(evidence_svg)}
  <line x1="84" y1="{height - 74}" x2="1516" y2="{height - 74}" stroke="#2b372d" stroke-width="2"/>
  <text x="84" y="{height - 38}" font-size="16" fill="#718071">证据构成：{_escape(modality_text)}{_escape(degraded_note)}</text>
  <text x="1516" y="{height - 38}" text-anchor="end" font-size="16" fill="#718071">Generated by bili-agent</text>
</svg>'''
