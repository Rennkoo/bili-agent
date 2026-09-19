from __future__ import annotations

import re

from .models import EvidenceSegment, PageTranscript, RetrievedSegment


_CJK_RE = re.compile(r"[\u4e00-\u9fff]+")
_LATIN_RE = re.compile(r"[A-Za-z0-9_]+")
_CJK_STOPWORDS = {
    "的", "了", "是", "在", "和", "与", "或", "及", "就", "都", "而", "也", "很", "还",
    "有", "把", "被", "对", "从", "到", "中", "上", "下", "这", "那", "一个", "我们", "你们",
    "他们", "什么", "怎么", "为什么", "请问", "可以", "如何", "是否", "以及", "然后", "因为", "所以",
}
_MERGE_GAP_SECONDS = 2.5


def _tokens(text: str) -> set[str]:
    """Create lightweight mixed-language search terms without external NLP deps."""
    normalized = text.lower()
    terms = {
        word for word in _LATIN_RE.findall(normalized)
        if word not in {"the", "and", "or", "is", "are"}
    }
    for run in _CJK_RE.findall(normalized):
        if len(run) == 1:
            if run not in _CJK_STOPWORDS:
                terms.add(run)
            continue
        # Bigrams are the main signal for Chinese keyword lookup; trigrams
        # help distinguish technical phrases such as "向量数据库".
        for width in (2, 3):
            terms.update(
                run[index:index + width]
                for index in range(len(run) - width + 1)
                if run[index:index + width] not in _CJK_STOPWORDS
            )
        if len(run) <= 8 and run not in _CJK_STOPWORDS:
            terms.add(run)
    return terms


def _phrase(text: str) -> str:
    return re.sub(r"\s+", "", text).lower()


def _join_text(left: str, right: str) -> str:
    left = left.strip()
    right = right.strip()
    if not left:
        return right
    if not right or right == left or right in left:
        return left
    if left in right:
        return right
    return f"{left} {right}"


def _merge_adjacent_hits(hits: list[RetrievedSegment]) -> list[RetrievedSegment]:
    """Merge nearby hits from the same page and modality before top-k ranking."""
    ordered = sorted(hits, key=lambda item: (item.page_index, item.modality, item.start, item.end))
    merged: list[RetrievedSegment] = []
    for hit in ordered:
        previous = merged[-1] if merged else None
        if (
            previous
            and previous.page_index == hit.page_index
            and previous.modality == hit.modality
            and hit.start <= previous.end + _MERGE_GAP_SECONDS
        ):
            global_values = [value for value in (previous.global_start, hit.global_start) if value is not None]
            end_values = [value for value in (previous.global_end, hit.global_end) if value is not None]
            updates = {
                "end": max(previous.end, hit.end),
                "global_start": min(global_values) if global_values else None,
                "global_end": max(end_values) if end_values else None,
                "text": _join_text(previous.text, hit.text),
                "score": max(previous.score, hit.score) + min(hit.score, 1.0) * 0.1,
            }
            merged[-1] = previous.model_copy(update=updates)
        else:
            merged.append(hit)
    return merged


def search_transcripts(
    transcripts: list[PageTranscript], query: str, top_k: int = 5
) -> list[RetrievedSegment]:
    query_tokens = _tokens(query)
    if not query_tokens:
        return []
    evidence = [
        EvidenceSegment(
            page_index=transcript.page.page_index,
            page_title=transcript.page.title,
            start=segment.start,
            end=segment.end,
            modality=transcript.source if transcript.source != "none" else "metadata",
            content=segment.text,
            source_label=transcript.source.upper(),
        )
        for transcript in transcripts
        for segment in transcript.segments
    ]
    return search_evidence(evidence, query, top_k)


def search_evidence(
    evidence: list[EvidenceSegment], query: str, top_k: int = 5
) -> list[RetrievedSegment]:
    query_tokens = _tokens(query)
    if not query_tokens:
        return []
    query_phrase = _phrase(query)
    hits: list[RetrievedSegment] = []
    for segment in evidence:
        text_tokens = _tokens(segment.content)
        overlap = len(query_tokens & text_tokens)
        exact = 2.0 if query_phrase and query_phrase in _phrase(segment.content) else 0.0
        score = overlap + exact + (0.25 if segment.modality in {"cc", "asr"} else 0.0)
        if score:
            hits.append(
                RetrievedSegment(
                    page_index=segment.page_index,
                    page_title=segment.page_title,
                    start=segment.start,
                    end=segment.end,
                    global_start=segment.global_start,
                    global_end=segment.global_end,
                    text=segment.content.strip(),
                    score=score,
                    modality=segment.modality,
                )
            )
    merged = _merge_adjacent_hits(hits)
    merged.sort(key=lambda item: (-item.score, item.page_index, item.start))
    return merged[:top_k]
