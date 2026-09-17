from bili_agent.models import Caption, PageInfo, PageTranscript
from bili_agent.retrieval import search_transcripts


def test_search_returns_timestamped_hit():
    page = PageInfo(page_index=0, cid=1, title="第一部分")
    transcript = PageTranscript(
        page=page,
        source="cc",
        segments=[Caption(start=12, end=15, text="这里介绍缓存和索引的关系。")],
    )
    hits = search_transcripts([transcript], "缓存")
    assert hits[0].start == 12
    assert "缓存" in hits[0].text
