from __future__ import annotations

import asyncio
import io
import logging
from dataclasses import dataclass
from pathlib import Path

from .asr import AudioDownloader
from .config import Settings
from .llm import LLMClient
from .models import EvidenceSegment, PageInfo

LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class VisualFrame:
    timestamp: float
    image_bytes: bytes


class VideoDownloader:
    """Download a small, directly decodable video for visual analysis."""

    def __init__(self, output_dir: Path):
        self.output_dir = output_dir

    async def download(self, url: str, page: PageInfo) -> Path:
        return await asyncio.to_thread(self._download_sync, url, page)

    def _download_sync(self, url: str, page: PageInfo) -> Path:
        try:
            from yt_dlp import YoutubeDL
        except ImportError as exc:
            raise RuntimeError("启用视觉分析需要安装 yt-dlp。") from exc

        self.output_dir.mkdir(parents=True, exist_ok=True)
        stem = f"page-{page.page_index + 1}-{page.cid}"
        options = {
            "format": "worst[ext=mp4]/worst",
            "outtmpl": str(self.output_dir / f"{stem}.%(ext)s"),
            "merge_output_format": "mp4",
            "noplaylist": True,
            "quiet": True,
            "no_warnings": True,
        }
        with YoutubeDL(options) as ydl:
            info = ydl.extract_info(url, download=True)
            prepared = Path(ydl.prepare_filename(info))
        if prepared.exists():
            return prepared
        matches = sorted(self.output_dir.glob(f"{stem}.*"))
        if matches:
            return matches[0]
        raise FileNotFoundError(f"yt-dlp 未找到视频文件: {stem}")


class KeyframeExtractor:
    """Uniformly sample frames through PyAV, keeping timestamps aligned."""

    async def extract(self, video_path: Path, duration: int, max_frames: int) -> list[VisualFrame]:
        return await asyncio.to_thread(self._extract_sync, video_path, duration, max_frames)

    @staticmethod
    def _extract_sync(video_path: Path, duration: int, max_frames: int) -> list[VisualFrame]:
        try:
            import av
        except ImportError as exc:
            raise RuntimeError("关键帧分析需要安装可选依赖: pip install -e '.[vision]'") from exc

        frames: list[VisualFrame] = []
        target_count = max(1, max_frames)
        expected_duration = max(float(duration), 1.0)
        next_target = 0.0
        step = expected_duration / target_count
        with av.open(str(video_path)) as container:
            stream = next((item for item in container.streams if item.type == "video"), None)
            if stream is None:
                return []
            time_base = float(stream.time_base or 1.0)
            for frame in container.decode(stream):
                timestamp = float(frame.pts * time_base) if frame.pts is not None else next_target
                if timestamp + 0.05 < next_target and len(frames) < target_count:
                    continue
                image = frame.to_image()
                buffer = io.BytesIO()
                image.save(buffer, format="JPEG", quality=72, optimize=True)
                frames.append(VisualFrame(timestamp=max(timestamp, 0.0), image_bytes=buffer.getvalue()))
                next_target += step
                if len(frames) >= target_count:
                    break
        return frames


def _local_ocr(image_bytes: bytes) -> str | None:
    """Use pytesseract when the user has it installed; absence is normal."""
    try:
        import pytesseract
        from PIL import Image
    except ImportError:
        return None
    try:
        try:
            text = pytesseract.image_to_string(Image.open(io.BytesIO(image_bytes)), lang="chi_sim+eng")
        except Exception:
            text = pytesseract.image_to_string(Image.open(io.BytesIO(image_bytes)), lang="eng")
        return " ".join(text.split()) or None
    except Exception:
        LOGGER.debug("本地 OCR 不可用，跳过该帧。", exc_info=True)
        return None


class MultimodalExtractor:
    def __init__(self, settings: Settings, llm: LLMClient):
        self.settings = settings
        self.llm = llm
        self.downloader = VideoDownloader(settings.media_cache_dir)
        self.frames = KeyframeExtractor()

    async def extract_page(self, video_url: str, page: PageInfo) -> list[EvidenceSegment]:
        page_url = f"{video_url}{'&' if '?' in video_url else '?'}p={page.page_index + 1}"
        try:
            video_path = await self.downloader.download(page_url, page)
            frames = await self.frames.extract(video_path, page.duration_seconds, self.settings.multimodal_max_frames)
        except Exception as exc:
            LOGGER.warning("第 %s P 的关键帧提取失败: %s", page.page_index + 1, exc)
            return []

        evidence: list[EvidenceSegment] = []
        for frame in frames:
            ocr_text = await asyncio.to_thread(_local_ocr, frame.image_bytes)
            if ocr_text:
                evidence.append(
                    EvidenceSegment(
                        page_index=page.page_index,
                        page_title=page.title,
                        start=frame.timestamp,
                        end=frame.timestamp,
                        modality="ocr",
                        content=f"画面文字：{ocr_text}",
                        confidence=0.75,
                        source_label="OCR",
                    )
                )
            vision_text = await self.llm.describe_image(
                frame.image_bytes,
                "请分析这张视频关键帧。简洁描述画面主体、可见文字、图表或代码，以及它可能表达的信息。"
                "只陈述画面中能观察到的内容，不要猜测看不见的上下文。",
            )
            if vision_text:
                evidence.append(
                    EvidenceSegment(
                        page_index=page.page_index,
                        page_title=page.title,
                        start=frame.timestamp,
                        end=frame.timestamp,
                        modality="vision",
                        content=vision_text,
                        confidence=0.8,
                        source_label="视觉分析",
                    )
                )
        return evidence
