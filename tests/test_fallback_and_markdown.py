import asyncio
from datetime import datetime, timezone

from bili_agent.config import Settings
from bili_agent.llm import LLMClient
from bili_agent.markdown import render_markdown
from bili_agent.models import AnalysisResult, Caption, EvidenceSegment, PageAnalysis, PageInfo, PageTranscript, VideoMetadata, VideoSummary


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


def test_no_key_fallback_summary_is_structured():
    page = PageInfo(page_index=0, cid=123, title="测试分P")
    transcript = PageTranscript(
        page=page,
        source="cc",
        segments=[Caption(start=4.5, end=7.0, text="这是字幕内容。")],
    )
    summary = asyncio.run(LLMClient(_settings()).summarize_page(page, transcript))
    assert summary.video_title == "测试分P"
    assert "降级内容" in summary.overall_summary
    assert summary.chapters[0].timestamp == "00:00:04"


def test_markdown_contains_metadata_timestamps_and_missing_caption_notice():
    page = PageInfo(page_index=0, cid=123, title="第一部分", duration_seconds=12)
    transcript = PageTranscript(
        page=page,
        source="cc",
        segments=[Caption(start=4, end=7, text="带时间戳的字幕")],
    )
    empty_page = PageInfo(page_index=1, cid=456, title="第二部分")
    empty_transcript = PageTranscript(
        page=empty_page,
        source="none",
        notice="该分P没有可用 CC 字幕；ASR 默认未启用。",
    )
    page_summary = LLMClient._fallback_summary(page, transcript)
    empty_summary = LLMClient._fallback_summary(empty_page, empty_transcript)
    metadata = VideoMetadata(
        bvid="BV1xx411c7mD",
        aid=170001,
        title="测试视频",
        author="测试 UP主",
        pubdate=datetime(2024, 1, 2, tzinfo=timezone.utc),
        duration_seconds=12,
        pages=[page, empty_page],
        url="https://www.bilibili.com/video/BV1xx411c7mD",
    )
    result = AnalysisResult(
        metadata=metadata,
        pages=[
            PageAnalysis(page=page, transcript=transcript, summary=page_summary),
            PageAnalysis(page=empty_page, transcript=empty_transcript, summary=empty_summary),
        ],
        summary=page_summary,
        timeline=[
            EvidenceSegment(
                page_index=0,
                page_title="第一部分",
                start=8,
                end=8,
                modality="ocr",
                content="画面文字：缓存策略",
                source_label="OCR",
            )
        ],
        degraded=True,
        degraded_reason="测试降级原因",
    )
    markdown = render_markdown(result)
    assert "测试 UP主" in markdown
    assert "`00:00:04` 带时间戳的字幕" in markdown
    assert "该分P没有可用 CC 字幕" in markdown
    assert "多模态证据时间线" in markdown
    assert "画面文字：缓存策略" in markdown
    assert "测试降级原因" in markdown


def test_fallback_overall_chapters_use_global_timestamps():
    page_one = PageInfo(page_index=0, cid=123, title="第一部分", duration_seconds=90)
    page_two = PageInfo(page_index=1, cid=456, title="第二部分", duration_seconds=45)
    metadata = VideoMetadata(
        bvid="BV1xx411c7mD",
        aid=170001,
        title="测试视频",
        pages=[page_one, page_two],
        duration_seconds=135,
        url="https://www.bilibili.com/video/BV1xx411c7mD",
    )
    summaries = [
        VideoSummary(
            video_title="第一部分",
            overall_summary="一",
            chapters=[{"timestamp": "00:00:10", "title": "一", "summary": "一"}],
        ),
        VideoSummary(
            video_title="第二部分",
            overall_summary="二",
            chapters=[{"timestamp": "00:00:05", "title": "二", "summary": "二"}],
        ),
    ]
    overall = LLMClient._fallback_overall(metadata, summaries)
    assert [chapter.timestamp for chapter in overall.chapters] == ["00:00:10", "00:01:35"]


def test_video_summary_skips_llm_without_content_evidence():
    class ExplodingClient(LLMClient):
        async def _chat_json(self, prompt):
            raise AssertionError("metadata-only input must not call LLM")

    page = PageInfo(page_index=0, cid=123, title="无字幕分P", duration_seconds=30)
    metadata = VideoMetadata(
        bvid="BV1xx411c7mD",
        aid=170001,
        title="无字幕视频",
        pages=[page],
        duration_seconds=30,
        url="https://www.bilibili.com/video/BV1xx411c7mD",
    )
    client = ExplodingClient(_settings())
    summary = asyncio.run(client.summarize_video(metadata, [VideoSummary(video_title=page.title, overall_summary="没有证据")], has_evidence=False))
    assert summary.knowledge_points == []
    assert "没有获取到" in summary.overall_summary
