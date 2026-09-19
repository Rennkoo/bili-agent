from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Awaitable, Callable

from .asr import AudioDownloader, FasterWhisperTranscriber
from .bilibili_client import BilibiliClient
from .config import Settings
from .llm import LLMClient, format_timestamp
from .multimodal import MultimodalExtractor
from .models import AnalysisResult, Answer, EvidenceSegment, PageAnalysis
from .parser import parse_video_input
from .retrieval import search_evidence, search_transcripts
from .skills import SkillRoute, classify_question, expand_with_history

LOGGER = logging.getLogger(__name__)


def _page_offsets(pages) -> dict[int, float]:
    offset = 0.0
    offsets: dict[int, float] = {}
    for page in sorted(pages, key=lambda item: item.page_index):
        offsets[page.page_index] = offset
        offset += max(float(page.duration_seconds), 0.0)
    return offsets


def _with_global_time(item: EvidenceSegment, offset: float) -> EvidenceSegment:
    return item.model_copy(
        update={
            "global_start": offset + item.start,
            "global_end": offset + item.end,
        }
    )


def _timestamp_range(start: float, end: float) -> str:
    begin = format_timestamp(start)
    finish = format_timestamp(end)
    return begin if finish == begin else f"{begin}-{finish}"


class BiliAgent:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or Settings.from_env()
        self.bilibili = BilibiliClient(self.settings)
        self.llm = LLMClient(self.settings)

    async def analyze(
        self,
        video_input: str,
        enable_asr: bool | None = None,
        enable_multimodal: bool | None = None,
        progress: Callable[[str, int, str], Awaitable[None]] | None = None,
    ) -> AnalysisResult:
        async def report(stage: str, percent: int, message: str) -> None:
            if progress:
                await progress(stage, percent, message)

        await report("metadata", 8, "正在获取视频信息和分P列表")
        identifier = parse_video_input(video_input)
        metadata, bili_video = await self.bilibili.fetch_metadata(identifier)
        await report("captions", 22, "正在获取 CC 字幕")
        use_asr = self.settings.asr_enabled if enable_asr is None else enable_asr
        transcriber = None
        downloader = None
        asr_notice = None
        if use_asr:
            try:
                transcriber = await asyncio.to_thread(
                    FasterWhisperTranscriber,
                    self.settings.asr_model,
                    self.settings.asr_device,
                    self.settings.asr_compute_type,
                )
                downloader = AudioDownloader(self.settings.asr_cache_dir)
            except Exception as exc:
                asr_notice = f"未能启用 ASR：{exc}"
                LOGGER.warning(asr_notice)
        transcripts = await self.bilibili.fetch_transcripts(
            metadata, bili_video, bool(transcriber and downloader), transcriber, downloader, asr_notice
        )
        await report("transcript", 48, "字幕/语音内容已整理，正在生成时间线")
        page_summaries = []
        pages = []
        timeline: list[EvidenceSegment] = []
        offsets = _page_offsets(metadata.pages)
        for transcript in transcripts:
            modality = transcript.source if transcript.source != "none" else "metadata"
            offset = offsets.get(transcript.page.page_index, 0.0)
            timeline.extend(
                _with_global_time(
                    EvidenceSegment(
                        page_index=transcript.page.page_index,
                        page_title=transcript.page.title,
                        start=segment.start,
                        end=segment.end,
                        modality=modality,
                        content=segment.text,
                        source_label="CC 字幕" if modality == "cc" else "ASR 转写",
                    ),
                    offset,
                )
                for segment in transcript.segments
            )
        use_multimodal = self.settings.multimodal_enabled if enable_multimodal is None else enable_multimodal
        if use_multimodal:
            await report("visual", 54, "正在提取关键帧、OCR 和画面信息")
            visual = MultimodalExtractor(self.settings, self.llm)
            for transcript in transcripts:
                visual_evidence = await visual.extract_page(metadata.url, transcript.page)
                offset = offsets.get(transcript.page.page_index, 0.0)
                timeline.extend(_with_global_time(item, offset) for item in visual_evidence)
                await report(
                    "visual",
                    min(70, 54 + int(16 * ((transcript.page.page_index + 1) / max(len(transcripts), 1)))),
                    f"正在分析第 {transcript.page.page_index + 1} P 的画面",
                )
        for transcript in transcripts:
            page_evidence = [item for item in timeline if item.page_index == transcript.page.page_index]
            page_summary = await self.llm.summarize_page(transcript.page, transcript, page_evidence)
            page_summaries.append(page_summary)
            pages.append(PageAnalysis(page=transcript.page, transcript=transcript, summary=page_summary))
            await report("summary", min(88, 52 + int(32 * (len(pages) / max(len(transcripts), 1)))), f"正在总结第 {transcript.page.page_index + 1} P")
        summary = await self.llm.summarize_video(metadata, page_summaries)
        await report("complete", 100, "分析完成")
        timeline.sort(
            key=lambda item: (
                item.global_start if item.global_start is not None else item.start,
                item.page_index,
                item.modality,
            )
        )
        return AnalysisResult(metadata=metadata, pages=pages, summary=summary, timeline=timeline, degraded=self.llm.degraded)

    async def ask(
        self,
        result: AnalysisResult,
        question: str,
        top_k: int = 5,
        history: list[dict[str, str]] | None = None,
    ) -> Answer:
        if not question.strip():
            raise ValueError("问题不能为空。")
        route = classify_question(question)
        direct_answer = self._direct_answer(result, route)
        if direct_answer is not None:
            return Answer(
                question=question,
                answer=direct_answer,
                sources=[],
                degraded=result.degraded,
                skill=route.name,
            )

        retrieval_question = expand_with_history(question, history)
        hits = search_evidence(result.timeline, retrieval_question, top_k=top_k) if result.timeline else search_transcripts([item.transcript for item in result.pages], retrieval_question, top_k=top_k)
        if not hits:
            return Answer(
                question=question,
                answer="字幕中没有检索到与问题明显相关的片段，无法可靠回答。",
                sources=[],
                degraded=True,
                skill=route.name,
            )
        context = "\n".join(
            f"[{hit.modality} · P{hit.page_index + 1} {hit.page_title} {_timestamp_range(hit.global_start if hit.global_start is not None else hit.start, hit.global_end if hit.global_end is not None else hit.end)}] {hit.text}"
            for hit in hits
        )
        history_text = "\n".join(
            f"{item.get('role', 'user')}: {item.get('content', '')}" for item in (history or [])[-4:]
        )
        answer_text = await self.llm.answer(question, context, result.summary, history=history_text)
        used_llm = bool(answer_text)
        if not answer_text:
            answer_text = "根据检索到的字幕片段：\n" + "\n".join(
                f"- [P{hit.page_index + 1} {hit.page_title} {_timestamp_range(hit.global_start if hit.global_start is not None else hit.start, hit.global_end if hit.global_end is not None else hit.end)}] {hit.text}" for hit in hits
            )
        citations = "；".join(
            dict.fromkeys(
                f"P{hit.page_index + 1} {hit.page_title} {_timestamp_range(hit.global_start if hit.global_start is not None else hit.start, hit.global_end if hit.global_end is not None else hit.end)}"
                for hit in hits
            )
        )
        if "来源" not in answer_text or not re.search(r"\b\d{2}:\d{2}:\d{2}\b", answer_text):
            answer_text += f"\n\n来源：{citations}"
        return Answer(
            question=question,
            answer=answer_text,
            sources=hits,
            degraded=self.llm.degraded or not used_llm,
            skill=route.name,
        )

    @staticmethod
    def _direct_answer(result: AnalysisResult, route: SkillRoute) -> str | None:
        metadata = result.metadata
        if route.name == "metadata":
            published = metadata.pubdate.strftime("%Y-%m-%d") if metadata.pubdate else "未知"
            return (
                f"标题：{metadata.title}\nUP主：{metadata.author}\n"
                f"发布时间：{published}\n总时长：{format_timestamp(metadata.duration_seconds)}\n"
                f"分P数：{len(metadata.pages)}\n链接：{metadata.url}"
            )
        if route.name == "summary":
            return result.summary.overall_summary
        if route.name == "timeline":
            if not result.summary.chapters:
                return "当前总结中没有可用的章节时间线。"
            return "\n".join(
                f"- {chapter.timestamp}｜{chapter.title}：{chapter.summary}"
                for chapter in result.summary.chapters
            )
        if route.name == "knowledge":
            if not result.summary.knowledge_points:
                return "当前总结中没有提取到明确知识点。"
            return "\n".join(
                f"- {point.term}：{point.explanation}"
                for point in result.summary.knowledge_points
            )
        return None
