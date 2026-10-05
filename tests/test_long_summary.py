import asyncio
from dataclasses import replace

from bili_agent.llm import LLMClient
from bili_agent.models import Caption, PageInfo, PageTranscript

from test_fallback_and_markdown import _settings


def _long_transcript(count: int = 205) -> PageTranscript:
    page = PageInfo(page_index=0, cid=123, title="长视频分P", duration_seconds=count * 10)
    return PageTranscript(
        page=page,
        source="asr",
        segments=[
            Caption(start=index * 10, end=index * 10 + 8, text=f"第 {index} 段内容")
            for index in range(count)
        ],
    )


def test_long_page_is_split_into_bounded_llm_requests():
    class FakeClient(LLMClient):
        def __init__(self):
            super().__init__(replace(_settings(), llm_chunk_segments=100, llm_chunk_concurrency=2))
            self._client = object()
            self.prompts = []

        async def _chat_json(self, prompt):
            self.prompts.append(prompt)
            return {
                "video_title": "长视频分P",
                "overall_summary": f"片段总结 {len(self.prompts)}",
                "chapters": [{
                    "timestamp": "00:00:10",
                    "title": f"章节 {len(self.prompts)}",
                    "summary": "片段摘要",
                    "key_points": [],
                }],
                "knowledge_points": [{"term": "共同概念", "explanation": "重复知识点"}],
            }

    client = FakeClient()
    transcript = _long_transcript()
    summary = asyncio.run(client.summarize_page(transcript.page, transcript))

    assert len(client.prompts) == 3
    assert len(summary.chapters) == 3
    assert len(summary.knowledge_points) == 1
    assert "片段总结" in summary.overall_summary
    assert all("第 0 段内容" not in prompt or len(prompt) < 16000 for prompt in client.prompts)


def test_failed_long_page_chunk_is_reported_as_partial_degradation():
    class PartialClient(LLMClient):
        def __init__(self):
            super().__init__(replace(_settings(), llm_chunk_segments=100))
            self._client = object()
            self.calls = 0

        async def _chat_json(self, prompt):
            self.calls += 1
            if self.calls == 2:
                raise RuntimeError("simulated timeout")
            return {
                "video_title": "长视频分P",
                "overall_summary": "可用片段",
                "chapters": [],
                "knowledge_points": [],
            }

    transcript = _long_transcript()
    summary = asyncio.run(PartialClient().summarize_page(transcript.page, transcript))
    assert summary.overall_summary.startswith("[部分降级]")
