from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class VideoIdentifier(BaseModel):
    model_config = ConfigDict(frozen=True)

    kind: Literal["bvid", "aid"]
    value: str
    canonical_url: str


class PageInfo(BaseModel):
    page_index: int = Field(ge=0)
    cid: int
    title: str
    duration_seconds: int = Field(default=0, ge=0)


class VideoMetadata(BaseModel):
    bvid: str
    aid: int
    title: str
    author: str = "未知"
    pubdate: datetime | None = None
    duration_seconds: int = Field(default=0, ge=0)
    description: str = ""
    pic: str | None = None
    pages: list[PageInfo] = Field(default_factory=list)
    url: str


class Caption(BaseModel):
    start: float = Field(ge=0)
    end: float = Field(ge=0)
    text: str


class PageTranscript(BaseModel):
    page: PageInfo
    source: Literal["cc", "asr", "none"]
    segments: list[Caption] = Field(default_factory=list)
    notice: str | None = None

    @property
    def text(self) -> str:
        return "\n".join(segment.text.strip() for segment in self.segments if segment.text.strip())


class EvidenceSegment(BaseModel):
    """A timestamped fact from one content modality."""

    page_index: int = Field(ge=0)
    page_title: str
    start: float = Field(ge=0)
    end: float = Field(ge=0)
    modality: Literal["cc", "asr", "ocr", "vision", "audio_event", "metadata"]
    content: str
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    source_label: str


class Chapter(BaseModel):
    timestamp: str
    title: str
    summary: str
    key_points: list[str] = Field(default_factory=list)


class KnowledgePoint(BaseModel):
    term: str
    explanation: str


class VideoSummary(BaseModel):
    video_title: str
    overall_summary: str
    chapters: list[Chapter] = Field(default_factory=list)
    knowledge_points: list[KnowledgePoint] = Field(default_factory=list)


class PageAnalysis(BaseModel):
    page: PageInfo
    transcript: PageTranscript
    summary: VideoSummary


class AnalysisResult(BaseModel):
    metadata: VideoMetadata
    pages: list[PageAnalysis]
    summary: VideoSummary
    timeline: list[EvidenceSegment] = Field(default_factory=list)
    degraded: bool = False


class RetrievedSegment(BaseModel):
    page_index: int
    page_title: str
    start: float
    end: float
    text: str
    score: float
    modality: str = "cc"


class Answer(BaseModel):
    question: str
    answer: str
    sources: list[RetrievedSegment] = Field(default_factory=list)
    degraded: bool = False
