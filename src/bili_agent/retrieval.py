from __future__ import annotations

import re

from .models import EvidenceSegment, PageTranscript, RetrievedSegment


def _tokens(text: str) -> set[str]:
    latin = {word.lower() for word in re.findall(r"[A-Za-z0-9_]+", text)}
    cjk = set(re.findall(r"[\u4e00-\u9fff]", text))
    return latin | cjk


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
    hits: list[RetrievedSegment] = []
    for segment in evidence:
        text_tokens = _tokens(segment.content)
        overlap = len(query_tokens & text_tokens)
        exact = 2.0 if query.strip() and query.strip().lower() in segment.content.lower() else 0.0
        score = overlap + exact + (0.25 if segment.modality in {"cc", "asr"} else 0.0)
        if score:
            hits.append(
                RetrievedSegment(
                    page_index=segment.page_index,
                    page_title=segment.page_title,
                    start=segment.start,
                    end=segment.end,
                    text=segment.content.strip(),
                    score=score,
                    modality=segment.modality,
                )
            )
    hits.sort(key=lambda item: (-item.score, item.page_index, item.start))
    return hits[:top_k]
