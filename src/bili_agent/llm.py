from __future__ import annotations

import asyncio
import json
import logging
import re
import base64
from typing import Any

from .config import Settings
from .models import Chapter, EvidenceSegment, KnowledgePoint, PageInfo, PageTranscript, VideoMetadata, VideoSummary

LOGGER = logging.getLogger(__name__)


def format_timestamp(seconds: float) -> str:
    total = max(0, int(seconds))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def _parse_timestamp(value: str) -> int | None:
    parts = [part for part in value.strip().split(":") if part.isdigit()]
    if len(parts) == 3:
        hours, minutes, seconds = (int(part) for part in parts)
        return hours * 3600 + minutes * 60 + seconds
    if len(parts) == 2:
        minutes, seconds = (int(part) for part in parts)
        return minutes * 60 + seconds
    return None


class LLMClient:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._client = None
        if settings.llm_api_key:
            try:
                from openai import AsyncOpenAI
                kwargs: dict[str, Any] = {"api_key": settings.llm_api_key}
                if settings.llm_base_url:
                    kwargs["base_url"] = settings.llm_base_url
                # Summary requests have a deterministic local fallback. Disable the SDK's
                # implicit retries so one unavailable endpoint cannot stall a long video
                # analysis for several timeout windows.
                self._client = AsyncOpenAI(
                    **kwargs,
                    timeout=settings.llm_timeout_seconds,
                    max_retries=0,
                )
            except ImportError:
                LOGGER.warning("未安装 openai，改用原文降级模式。")
        else:
            LOGGER.warning("未设置 LLM_API_KEY，将使用字幕原文截断作为降级内容。")

    @property
    def degraded(self) -> bool:
        return self._client is None

    @property
    def vision_available(self) -> bool:
        """Whether the configured OpenAI-compatible endpoint can receive images."""
        return self._client is not None

    async def summarize_page(
        self,
        page: PageInfo,
        transcript: PageTranscript,
        evidence: list[EvidenceSegment] | None = None,
    ) -> VideoSummary:
        evidence = evidence or []
        evidence_text = "\n".join(
            f"[{item.source_label} {format_timestamp(item.start)}] {item.content}" for item in evidence
        )
        if not transcript.text and not evidence_text:
            return self._fallback_summary(
                page,
                transcript,
                evidence,
                reason="没有获取到 CC、ASR、OCR 或视觉证据，无法可靠总结。",
            )

        if self._client is None:
            return self._fallback_summary(page, transcript, evidence)
        if len(transcript.segments) > self.settings.llm_chunk_segments or len(evidence) > self.settings.llm_chunk_segments:
            return await self._summarize_page_in_chunks(page, transcript, evidence)
        # A long page can contain thousands of ASR segments. Keep the prompt
        # representative while the full transcript remains available to notes
        # and retrieval. Head/tail coverage is more useful than a head-only cut.
        prompt_limit = self.settings.max_transcript_chars
        if len(transcript.segments) > 600 or len(evidence) > 600:
            prompt_limit = max(6000, prompt_limit // 2)
        prompt = (
            "请分析以下 B 站视频分P内容证据。证据可能来自 CC 字幕、ASR、OCR 或视觉分析。"
            "只输出合法 JSON，不要 Markdown 代码围栏，字段必须为 "
            "video_title、overall_summary、chapters、knowledge_points。chapters 每项包含 "
            "timestamp、title、summary、key_points；knowledge_points 每项包含 term、explanation。"
            "overall_summary 约 200 字，必须忠实于提供的内容证据，不要臆造。\n\n"
            f"分P标题：{page.title}\n字幕或 ASR：\n{self._clip(transcript.text, prompt_limit)}\n"
            f"OCR/视觉证据：\n{self._clip(evidence_text, prompt_limit)}"
        )
        try:
            data = await self._chat_json(prompt)
            return self._validate_summary(data, page.title)
        except Exception:
            LOGGER.exception("第 %s P 的 LLM 总结失败，使用降级内容。", page.page_index + 1)
            return self._fallback_summary(
                page,
                transcript,
                evidence,
                reason="LLM 总结请求失败，已自动使用内容证据降级。",
            )

    async def _summarize_page_in_chunks(
        self,
        page: PageInfo,
        transcript: PageTranscript,
        evidence: list[EvidenceSegment],
    ) -> VideoSummary:
        """Summarize long pages in bounded time windows.

        The complete transcript remains in the result. Only the LLM prompt is
        chunked, which prevents a long page from timing out as one oversized
        request while preserving time-aligned chapters and retrieval evidence.
        """
        chunk_size = max(self.settings.llm_chunk_segments, 100)
        chunks = [
            transcript.segments[index : index + chunk_size]
            for index in range(0, len(transcript.segments), chunk_size)
        ]
        if not chunks:
            return self._fallback_summary(page, transcript, evidence)

        semaphore = asyncio.Semaphore(max(self.settings.llm_chunk_concurrency, 1))

        async def summarize_chunk(index: int, segments: list) -> tuple[VideoSummary, bool]:
            start = segments[0].start
            end = segments[-1].end
            segment_text = "\n".join(
                f"[{format_timestamp(item.start)}] {item.text}" for item in segments
            )
            chunk_evidence = [
                item
                for item in evidence
                if item.end >= start and item.start <= end
            ]
            evidence_text = "\n".join(
                f"[{item.source_label} {format_timestamp(item.start)}] {item.content}"
                for item in chunk_evidence
            )
            prompt = (
                "请总结以下 B 站视频分P的一个连续时间片段，只输出合法 JSON，不要 Markdown 代码围栏。"
                "字段必须为 video_title、overall_summary、chapters、knowledge_points；"
                "chapters 每项包含 timestamp、title、summary、key_points；"
                "knowledge_points 每项包含 term、explanation。"
                "时间戳必须使用原视频分P的相对时间，不能从 00:00:00 重新开始。"
                "只依据证据，不要臆造。\n\n"
                f"分P标题：{page.title}\n时间片段：{format_timestamp(start)} - {format_timestamp(end)}\n"
                f"字幕或 ASR：\n{self._clip(segment_text, self.settings.max_transcript_chars // 2)}\n"
                f"OCR/视觉证据：\n{self._clip(evidence_text, self.settings.max_transcript_chars // 3)}"
            )
            async with semaphore:
                try:
                    data = await self._chat_json(prompt)
                    return self._validate_summary(data, page.title), False
                except Exception:
                    LOGGER.exception(
                        "第 %s P 的第 %s 个 LLM 分块总结失败，使用该时间片段降级内容。",
                        page.page_index + 1,
                        index + 1,
                    )
                    fallback_transcript = PageTranscript(page=page, source=transcript.source, segments=segments)
                    return self._fallback_summary(
                        page,
                        fallback_transcript,
                        chunk_evidence,
                        reason="该时间片段 LLM 请求失败，已使用内容证据降级。",
                    ), True

        results = await asyncio.gather(
            *(summarize_chunk(index, chunk) for index, chunk in enumerate(chunks))
        )
        summaries = [item[0] for item in results]
        partially_degraded = any(item[1] for item in results)
        chapters = [chapter for summary in summaries for chapter in summary.chapters]
        points: list[KnowledgePoint] = []
        seen_terms: set[str] = set()
        for summary in summaries:
            for point in summary.knowledge_points:
                key = point.term.strip().casefold()
                if key and key not in seen_terms:
                    seen_terms.add(key)
                    points.append(point)
        overview = "；".join(
            summary.overall_summary.removeprefix("[降级内容] ").strip()
            for summary in summaries
            if summary.overall_summary.strip()
        )
        if partially_degraded:
            overview = "[部分降级] " + overview
        return VideoSummary(
            video_title=page.title,
            overall_summary=overview[:1600],
            chapters=chapters,
            knowledge_points=points,
        )

    async def rerank_transcript(self, transcript: PageTranscript) -> PageTranscript:
        """Choose among multilingual ASR candidates using page-level context."""
        candidates = [
            (index, segment)
            for index, segment in enumerate(transcript.segments)
            if segment.alternatives
        ]
        if not candidates or self._client is None:
            return transcript
        lines = []
        for index, segment in candidates[:120]:
            options = {segment.language or "primary": segment.text, **segment.alternatives}
            rendered = " | ".join(f"{language}: {text}" for language, text in options.items())
            lines.append(f"{index}: {rendered}")
        prompt = (
            "请从每个 ASR 候选中选择最符合音频语境的一项。只输出合法 JSON，格式为 "
            '{"selections":[{"index":0,"text":"候选原文"}]}。只能选择已有候选文本，不能翻译、改写或臆造。'
            "选择标准：语义完整、语言连贯、符合相邻上下文；外语专有名词保留原文。\n\n"
            f"分P：{transcript.page.title}\n候选片段：\n{self._clip(chr(10).join(lines))}"
        )
        try:
            data = await self._chat_json(prompt)
            allowed = {}
            for index, segment in candidates:
                allowed[index] = {segment.text, *segment.alternatives.values()}
            selections = {}
            for item in data.get("selections", []):
                if not isinstance(item, dict) or not str(item.get("index", "")).isdigit():
                    continue
                index = int(item["index"])
                text = str(item.get("text", ""))
                if index in allowed and text in allowed[index]:
                    selections[index] = text
            updated = [
                segment.model_copy(update={"text": selections.get(index, segment.text)})
                for index, segment in enumerate(transcript.segments)
            ]
            return transcript.model_copy(update={"segments": updated})
        except Exception:
            LOGGER.exception("第 %s P 的多语言候选重排失败，保留自动识别结果。", transcript.page.page_index + 1)
            return transcript

    async def summarize_video(
        self,
        metadata: VideoMetadata,
        page_summaries: list[VideoSummary],
        has_evidence: bool = True,
    ) -> VideoSummary:
        if not has_evidence:
            return self._fallback_overall(
                metadata,
                page_summaries,
                reason="没有获取到 CC、ASR、OCR 或视觉证据，无法可靠总结。",
            )
        if self._client is None:
            return self._fallback_overall(metadata, page_summaries)
        offset = 0
        digest_parts = []
        for index, summary in enumerate(page_summaries):
            page = metadata.pages[index] if index < len(metadata.pages) else None
            local_chapters = "；".join(
                f"{chapter.timestamp} {chapter.title}" for chapter in summary.chapters
            ) or "无明确章节"
            digest_parts.append(
                f"分P {index + 1}《{summary.video_title}》（全局起点 {format_timestamp(offset)}，"
                f"本P时长 {format_timestamp(page.duration_seconds if page else 0)}）："
                f"{summary.overall_summary}\n本P章节（时间戳为本P相对时间）：{local_chapters}"
            )
            offset += page.duration_seconds if page else 0
        digest = "\n\n".join(digest_parts)
        prompt = (
            "请把以下各分P总结合并成一个视频级总结，只输出合法 JSON，不要 Markdown 代码围栏。"
            "字段必须为 video_title、overall_summary、chapters、knowledge_points；章节必须保留或合理合并时间戳。"
            "请将 chapters.timestamp 统一输出为相对于整个视频的全局时间，不要输出本P相对时间；"
            "每项包含 timestamp、title、summary、key_points；知识点每项包含 term、explanation。"
            "overall_summary 约 200 字。\n\n"
            f"视频标题：{metadata.title}\n{self._clip(digest)}"
        )
        try:
            data = await self._chat_json(prompt)
            return self._validate_summary(data, metadata.title)
        except Exception:
            LOGGER.exception("视频级 LLM 总结失败，使用降级内容。")
            return self._fallback_overall(
                metadata,
                page_summaries,
                reason="视频级 LLM 总结请求失败，已自动按分P内容证据汇总。",
            )

    async def answer(
        self,
        question: str,
        context: str,
        summary: VideoSummary,
        history: str = "",
    ) -> str | None:
        if self._client is None:
            return None
        prompt = (
            "你是视频内容问答助手。仅依据提供的内容证据片段（字幕、ASR、OCR、视觉分析）和总结回答，"
            "不能确定时明确说不知道。"
            "回答简洁、具体，并在相关事实后标注 [分P标题 时间戳]。不要编造来源。\n\n"
            f"问题：{question}\n视频总结：{summary.overall_summary}\n内容证据片段：\n{context}"
        )
        if history:
            prompt += f"\n\n最近对话上下文（只用于理解省略指代，不作为事实来源）：\n{history}"
        try:
            response = await self._client.chat.completions.create(
                model=self.settings.llm_model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.2,
            )
            return (response.choices[0].message.content or "").strip() or None
        except Exception:
            LOGGER.exception("LLM 问答失败，使用检索片段降级回答。")
            return None

    async def describe_image(self, image_bytes: bytes, prompt: str) -> str | None:
        """Ask a vision-capable OpenAI-compatible model about one video frame."""
        if self._client is None:
            return None
        encoded = base64.b64encode(image_bytes).decode("ascii")
        try:
            response = await self._client.chat.completions.create(
                model=self.settings.vision_model or self.settings.llm_model,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt},
                            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{encoded}"}},
                        ],
                    }
                ],
                temperature=0.1,
            )
            return (response.choices[0].message.content or "").strip() or None
        except Exception:
            LOGGER.exception("视频关键帧视觉分析失败。")
            return None

    async def _chat_json(self, prompt: str) -> dict[str, Any]:
        common = {
            "model": self.settings.llm_model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.2,
        }
        try:
            response = await self._client.chat.completions.create(
                **common, response_format={"type": "json_object"}
            )
        except Exception as exc:
            # Some OpenAI-compatible gateways reject response_format, but a timeout
            # or transport error should not trigger a second equally slow request.
            error_text = str(exc).lower()
            error_type = type(exc).__name__.lower()
            format_error = "response_format" in error_text or "json_object" in error_text
            known_bad_request = error_type in {"badrequesterror", "unprocessableentityerror"}
            if not (format_error or known_bad_request):
                raise
            response = await self._client.chat.completions.create(**common)
        content = response.choices[0].message.content or "{}"
        content = re.sub(r"^\s*```(?:json)?\s*|\s*```\s*$", "", content, flags=re.IGNORECASE)
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            start, end = content.find("{"), content.rfind("}")
            if start < 0 or end <= start:
                raise
            return json.loads(content[start : end + 1])

    @staticmethod
    def _validate_summary(data: dict[str, Any], title: str) -> VideoSummary:
        data = dict(data)
        data["video_title"] = title
        data.setdefault("overall_summary", "暂无整体概述。")
        data.setdefault("chapters", [])
        data.setdefault("knowledge_points", [])
        return VideoSummary.model_validate(data)

    def _clip(self, text: str, limit: int | None = None) -> str:
        limit = limit or self.settings.max_transcript_chars
        if len(text) <= limit:
            return text
        head = max(limit * 2 // 3, 1)
        tail = max(limit - head, 1)
        return text[:head] + "\n[中间内容已截取，完整内容保存在笔记中]\n" + text[-tail:]

    @staticmethod
    def _fallback_summary(
        page: PageInfo,
        transcript: PageTranscript,
        evidence: list[EvidenceSegment] | None = None,
        reason: str = "未配置 LLM_API_KEY，以下为内容证据截断。",
    ) -> VideoSummary:
        evidence = evidence or []
        evidence_text = "\n".join(item.content for item in evidence)
        snippet = transcript.text[:800] if transcript.text else evidence_text[:800]
        snippet = snippet or "该分P没有可用字幕或视觉证据。"
        first = transcript.segments[0].start if transcript.segments else (evidence[0].start if evidence else 0)
        return VideoSummary(
            video_title=page.title,
            overall_summary=f"[降级内容] {reason}{snippet}",
            chapters=[Chapter(timestamp=format_timestamp(first), title="内容证据摘录", summary=snippet, key_points=[])],
            knowledge_points=[],
        )

    @staticmethod
    def _fallback_overall(
        metadata: VideoMetadata,
        page_summaries: list[VideoSummary],
        reason: str = "未配置 LLM_API_KEY，按分P内容证据汇总。",
    ) -> VideoSummary:
        text = "\n\n".join(summary.overall_summary for summary in page_summaries)
        chapters = []
        offset = 0
        for index, summary in enumerate(page_summaries):
            for chapter in summary.chapters:
                local_seconds = _parse_timestamp(chapter.timestamp)
                timestamp = (
                    format_timestamp(offset + local_seconds)
                    if local_seconds is not None
                    else chapter.timestamp
                )
                chapters.append(chapter.model_copy(update={"timestamp": timestamp}))
            if index < len(metadata.pages):
                offset += metadata.pages[index].duration_seconds
        points = [point for summary in page_summaries for point in summary.knowledge_points]
        return VideoSummary(
            video_title=metadata.title,
            overall_summary=f"[降级内容] {reason}{text[:1200]}",
            chapters=chapters,
            knowledge_points=points,
        )
