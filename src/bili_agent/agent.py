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
        page_indices: list[int] | None = None,
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
        if page_indices is not None:
            selected = set(page_indices)
            pages = [page for page in metadata.pages if page.page_index in selected]
            if not pages:
                raise ValueError("至少选择一个有效分P。")
            metadata = metadata.model_copy(
                update={
                    "pages": pages,
                    "duration_seconds": sum(page.duration_seconds for page in pages),
                }
            )
        await report("captions", 22, "正在获取 CC 字幕")
        use_asr = self.settings.asr_enabled if enable_asr is None else enable_asr
        transcriber = None
        downloader = None
        asr_notice = None
        if use_asr:
            await report("asr", 18, "正在准备 ASR 音频转写能力")
            try:
                transcriber = await asyncio.to_thread(
                    FasterWhisperTranscriber,
                    self.settings.asr_model,
                    self.settings.asr_device,
                    self.settings.asr_compute_type,
                    self.settings.asr_beam_size,
                    self.settings.asr_best_of,
                    self.settings.asr_language,
                    self.settings.asr_candidate_languages,
                    self.settings.asr_rerank_mode,
                )
                downloader = AudioDownloader(self.settings.asr_cache_dir, self.settings.asr_audio_format)
            except Exception as exc:
                asr_notice = f"未能启用 ASR：{exc}"
                LOGGER.warning(asr_notice)
        transcripts = await self.bilibili.fetch_transcripts(
            metadata,
            bili_video,
            bool(transcriber and downloader),
            transcriber,
            downloader,
            asr_notice,
            progress=lambda phase, current, total, message: report(
                "captions" if phase == "cc" else "asr",
                (22 + int(20 * current / max(total, 1)))
                if phase == "cc"
                else (
                    42 + int(6 * min(max(current / max(total, 1), 0.0), 1.0))
                    if phase == "asr_progress"
                    else 42 + int(6 * current / max(total, 1))
                ),
                message,
            ),
        )
        if self.settings.asr_rerank_mode == "llm":
            transcripts = await asyncio.gather(
                *(self.llm.rerank_transcript(transcript) for transcript in transcripts)
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
        summary = await self.llm.summarize_video(metadata, page_summaries, has_evidence=bool(timeline))
        await report("complete", 100, "分析完成")
        timeline.sort(
            key=lambda item: (
                item.global_start if item.global_start is not None else item.start,
                item.page_index,
                item.modality,
            )
        )
        page_summary_degraded = any(
            page.summary.overall_summary.startswith("[降级内容]") for page in pages
        )
        overall_summary_degraded = summary.overall_summary.startswith("[降级内容]")
        degraded_reason = None
        if not timeline:
            degraded_reason = "没有获取到 CC、ASR、OCR 或视觉证据，当前仅保留元数据降级结果。"
        elif self.llm.degraded:
            degraded_reason = "未配置 LLM_API_KEY，当前使用内容证据截断降级结果。"
        elif page_summary_degraded and overall_summary_degraded:
            degraded_reason = "部分分P和视频级 LLM 总结失败，已使用内容证据降级。"
        elif page_summary_degraded:
            degraded_reason = "部分分P的 LLM 总结请求失败，已使用内容证据降级；视频级总结仍已完成。"
        elif overall_summary_degraded:
            degraded_reason = "视频级 LLM 总结请求失败，已按分P内容证据汇总。"
        return AnalysisResult(
            metadata=metadata,
            pages=pages,
            summary=summary,
            timeline=timeline,
            degraded=self.llm.degraded or not timeline or page_summary_degraded or overall_summary_degraded,
            degraded_reason=degraded_reason,
        )

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
            answer_text = "根据检索到的内容证据片段：\n" + "\n".join(
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
