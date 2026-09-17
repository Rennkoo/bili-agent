from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Protocol

from .models import Caption, PageInfo

LOGGER = logging.getLogger(__name__)


class ASRTranscriber(Protocol):
    async def transcribe(self, audio_path: Path) -> list[Caption]: ...


class AudioDownloader:
    """Download only the best available audio through yt-dlp."""

    def __init__(self, output_dir: Path):
        self.output_dir = output_dir

    async def download(self, url: str, page: PageInfo) -> Path:
        return await asyncio.to_thread(self._download_sync, url, page)

    def _download_sync(self, url: str, page: PageInfo) -> Path:
        try:
            from yt_dlp import YoutubeDL
        except ImportError as exc:
            raise RuntimeError("启用 ASR 需要安装 yt-dlp。") from exc

        self.output_dir.mkdir(parents=True, exist_ok=True)
        stem = f"page-{page.page_index + 1}-{page.cid}"
        options = {
            "format": "bestaudio/best",
            "outtmpl": str(self.output_dir / f"{stem}.%(ext)s"),
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
        raise FileNotFoundError(f"yt-dlp 未找到下载后的音频文件: {stem}")


class FasterWhisperTranscriber:
    def __init__(self, model_name: str, device: str, compute_type: str):
        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:
            raise RuntimeError("启用 ASR 需要安装可选依赖: pip install -e '.[asr]'") from exc
        self._model = WhisperModel(model_name, device=device, compute_type=compute_type)

    async def transcribe(self, audio_path: Path) -> list[Caption]:
        return await asyncio.to_thread(self._transcribe_sync, audio_path)

    def _transcribe_sync(self, audio_path: Path) -> list[Caption]:
        segments, _ = self._model.transcribe(str(audio_path), vad_filter=True)
        return [Caption(start=float(item.start), end=float(item.end), text=item.text.strip()) for item in segments]
