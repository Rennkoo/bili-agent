import asyncio
from datetime import datetime, timezone

from bili_agent.agent import BiliAgent
from bili_agent.config import Settings
from bili_agent.models import AnalysisResult, Chapter, PageAnalysis, PageInfo, PageTranscript, VideoMetadata, VideoSummary, KnowledgePoint
from bili_agent.skills import classify_question, expand_with_history


def _settings() -> Settings:
    return Settings(
        llm_api_key=None,
        llm_base_url=None,
        llm_model="gpt-4o-mini",
        bili_sessdata=None,
        bili_bili_jct=None,
        bili_buvid3=None,
        asr_enabled=False,
        asr_model="small",
        asr_device="cpu",
        asr_compute_type="int8",
        asr_cache_dir=".cache",
    )


def _result() -> AnalysisResult:
    page = PageInfo(page_index=0, cid=1, title="第一部分", duration_seconds=90)
    transcript = PageTranscript(page=page, source="cc")
    summary = VideoSummary(
        video_title="测试视频",
        overall_summary="这是一个关于缓存策略的测试总结。",
        chapters=[Chapter(timestamp="00:00:10", title="缓存基础", summary="介绍缓存和索引。")],
        knowledge_points=[KnowledgePoint(term="缓存", explanation="减少重复读取，提高访问速度。")],
    )
    metadata = VideoMetadata(
        bvid="BVTEST",
        aid=1,
        title="测试视频",
        author="测试 UP主",
        pubdate=datetime(2024, 1, 2, tzinfo=timezone.utc),
        duration_seconds=90,
        pages=[page],
        url="https://www.bilibili.com/video/BVTEST",
    )
    return AnalysisResult(
        metadata=metadata,
        pages=[PageAnalysis(page=page, transcript=transcript, summary=summary)],
        summary=summary,
    )


def test_question_routes_to_direct_skills():
    assert classify_question("这个视频的时长是多少？").name == "metadata"
    assert classify_question("列出章节时间线").name == "timeline"
    assert classify_question("提取三个知识点").name == "knowledge"
    assert classify_question("这个视频主要讲了什么？").name == "summary"
    assert classify_question("根据字幕，为什么要这样做？").name == "evidence_qa"


def test_short_followup_expands_with_recent_question():
    expanded = expand_with_history(
        "为什么？",
        [{"role": "user", "content": "视频为什么要使用缓存？"}],
    )
    assert "视频为什么要使用缓存" in expanded
    assert expanded.endswith("为什么？")


def test_direct_skill_answer_does_not_need_llm():
    answer = asyncio.run(BiliAgent(_settings()).ask(_result(), "提取知识点"))
    assert answer.skill == "knowledge"
    assert "缓存" in answer.answer
    assert answer.sources == []
