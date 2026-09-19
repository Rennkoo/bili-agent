from bili_agent.agent import _page_offsets
from bili_agent.models import Caption, EvidenceSegment, PageInfo, PageTranscript
from bili_agent.retrieval import search_evidence, search_transcripts


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


def test_chinese_bigram_and_long_phrase_search():
    page = PageInfo(page_index=0, cid=1, title="检索测试")
    transcript = PageTranscript(
        page=page,
        source="cc",
        segments=[
            Caption(start=1, end=2, text="这里介绍向量数据库的索引结构。"),
            Caption(start=4, end=5, text="最后讨论部署和监控。"),
        ],
    )
    assert "向量数据库" in search_transcripts([transcript], "向量数据库")[0].text
    assert "索引结构" in search_transcripts([transcript], "索引结构怎么设计")[0].text


def test_adjacent_same_modality_hits_are_merged():
    evidence = [
        EvidenceSegment(page_index=0, page_title="第一部分", start=10, end=11, global_start=10, global_end=11, modality="cc", content="介绍缓存", source_label="CC 字幕"),
        EvidenceSegment(page_index=0, page_title="第一部分", start=11.5, end=13, global_start=11.5, global_end=13, modality="cc", content="和索引的关系", source_label="CC 字幕"),
    ]
    hits = search_evidence(evidence, "缓存索引")
    assert len(hits) == 1
    assert hits[0].start == 10
    assert hits[0].end == 13
    assert hits[0].global_start == 10
    assert hits[0].global_end == 13
    assert "缓存" in hits[0].text and "索引" in hits[0].text


def test_different_page_hits_stay_separate():
    evidence = [
        EvidenceSegment(page_index=0, page_title="第一部分", start=1, end=2, modality="cc", content="缓存", source_label="CC 字幕"),
        EvidenceSegment(page_index=1, page_title="第二部分", start=1, end=2, modality="cc", content="缓存", source_label="CC 字幕"),
    ]
    hits = search_evidence(evidence, "缓存", top_k=5)
    assert len(hits) == 2


def test_page_offsets_build_one_global_timeline():
    pages = [
        PageInfo(page_index=0, cid=1, title="第一部分", duration_seconds=90),
        PageInfo(page_index=1, cid=2, title="第二部分", duration_seconds=45),
        PageInfo(page_index=2, cid=3, title="第三部分", duration_seconds=30),
    ]
    assert _page_offsets(pages) == {0: 0.0, 1: 90.0, 2: 135.0}
