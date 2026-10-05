from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from .models import Caption, PageInfo

LOGGER = logging.getLogger(__name__)


class AudioDownloadError(RuntimeError):
    """A user-facing failure while retrieving a page's audio."""

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


class ASRTranscriber(Protocol):
    async def transcribe(self, audio_path: Path) -> list[Caption]: ...


class AudioDownloader:
    """Download only the best available audio through yt-dlp."""

    def __init__(self, output_dir: Path, format_selector: str = "worstaudio/bestaudio"):
        self.output_dir = output_dir
        self.format_selector = format_selector

    async def download(self, url: str, page: PageInfo) -> Path:
        return await asyncio.to_thread(self._download_sync, url, page)

    def _download_sync(self, url: str, page: PageInfo) -> Path:
        try:
            from yt_dlp import YoutubeDL
        except ImportError as exc:
            raise RuntimeError("启用 ASR 需要安装 yt-dlp。") from exc

        self.output_dir.mkdir(parents=True, exist_ok=True)
        stem = f"page-{page.page_index + 1}-{page.cid}"
        cached = sorted(
            path
            for path in self.output_dir.glob(f"{stem}.*")
            if path.suffix not in {".part", ".ytdl"}
        )
        if cached:
            LOGGER.info("复用已缓存音频: %s", cached[0])
            return cached[0]
        selectors = self._format_selectors()
        last_error: Exception | None = None
        for selector in selectors:
            options = {
                "format": selector,
                "outtmpl": str(self.output_dir / f"{stem}.%(ext)s"),
                "noplaylist": True,
                "quiet": True,
                "no_warnings": True,
            }
            try:
                LOGGER.info("尝试下载 P%s 音频格式: %s", page.page_index + 1, selector)
                with YoutubeDL(options) as ydl:
                    info = ydl.extract_info(url, download=True)
                    prepared = Path(ydl.prepare_filename(info))
                if prepared.exists():
                    return prepared
                matches = sorted(self.output_dir.glob(f"{stem}.*"))
                if matches:
                    return matches[0]
                raise FileNotFoundError(f"yt-dlp 未找到下载后的音频文件: {stem}")
            except Exception as exc:
                last_error = exc
                classified = self.classify_error(exc)
                if classified.code in {"restricted", "blocked"}:
                    raise classified from exc
                LOGGER.warning("P%s 音频格式 %s 不可用，将尝试备用格式: %s", page.page_index + 1, selector, exc)

        if last_error is not None:
            raise self.classify_error(last_error) from last_error
        raise AudioDownloadError("download_failed", f"无法下载第 {page.page_index + 1} P 的音频。")

    def _format_selectors(self) -> list[str]:
        """Prefer the configured selector, then tolerate Bilibili format drift."""
        candidates = [
            self.format_selector,
            "worst[acodec!=none]/worstaudio/bestaudio",
            "bestaudio[ext=m4a]/bestaudio/worst[acodec!=none]",
        ]
        return list(dict.fromkeys(item.strip() for item in candidates if item and item.strip()))

    @staticmethod
    def classify_error(exc: Exception) -> AudioDownloadError:
        text = str(exc)
        normalized = text.lower()
        if any(
            marker in normalized
            for marker in (
                "only preview format is available",
                "premium member",
                "become a premium",
                "大会员",
                "会员专享",
                "登录后观看",
            )
        ):
            return AudioDownloadError(
                "restricted",
                "B站只提供预览或会员权限，无法下载完整音频；请登录有权限的账号后重试。",
            )
        if "requested format is not available" in normalized or "no video formats found" in normalized:
            return AudioDownloadError(
                "format_unavailable",
                "B站当前没有可用的音频格式，可能是地区、登录状态或视频权限限制。",
            )
        if "ffmpeg" in normalized:
            return AudioDownloadError("ffmpeg_missing", "音频处理需要 ffmpeg，请先安装并加入 PATH。")
        return AudioDownloadError("download_failed", f"音频下载失败：{text[:240]}")


class FasterWhisperTranscriber:
    def __init__(
        self,
        model_name: str,
        device: str,
        compute_type: str,
        beam_size: int = 1,
        best_of: int = 1,
        language: str | None = None,
        candidate_languages: tuple[str, ...] = (),
        rerank_mode: str = "none",
    ):
        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:
            raise RuntimeError("启用 ASR 需要安装可选依赖: pip install -e '.[asr]'") from exc
        self._whisper_model = WhisperModel
        self._model_name = model_name
        self._device = device
        self._compute_type = compute_type
        self._beam_size = beam_size
        self._best_of = best_of
        self._language = language
        self._candidate_languages = candidate_languages
        self._rerank_mode = rerank_mode
        self._model = None

    async def transcribe(self, audio_path: Path) -> list[Caption]:
        return await self.transcribe_with_progress(audio_path)

    async def transcribe_with_progress(
        self,
        audio_path: Path,
        progress: Callable[[float, float], None] | None = None,
    ) -> list[Caption]:
        return await asyncio.to_thread(self._transcribe_sync, audio_path, progress)

    def _transcribe_sync(
        self,
        audio_path: Path,
        progress: Callable[[float, float], None] | None = None,
    ) -> list[Caption]:
        if self._model is None:
            LOGGER.info("正在加载 faster-whisper 模型: %s", self._model_name)
            self._model = self._whisper_model(
                self._model_name,
                device=self._device,
                compute_type=self._compute_type,
            )
        def decode(language: str | None):
            segments, info = self._model.transcribe(
                str(audio_path),
                language=language,
                vad_filter=True,
                beam_size=self._beam_size,
                best_of=self._best_of,
                condition_on_previous_text=False,
            )
            decoded = []
            for item in segments:
                decoded.append(
                    {
                        "start": float(item.start),
                        "end": float(item.end),
                        "text": item.text.strip(),
                        "score": float(getattr(item, "avg_logprob", -10.0) or -10.0),
                    }
                )
            return decoded, info

        decoded, info = decode(self._language)
        detected_language = getattr(info, "language", None) or "auto"
        LOGGER.info(
            "faster-whisper 语言: %s（配置: %s）",
            detected_language,
            self._language or "auto",
        )
        duration = float(getattr(info, "duration", 0.0) or 0.0)
        result: list[Caption] = []
        candidates: dict[str, list[dict]] = {}
        if self._candidate_languages:
            for language in self._candidate_languages:
                if language == self._language or language == detected_language:
                    continue
                LOGGER.info("生成 ASR 候选语言转写: %s", language)
                candidates[language], _ = decode(language)

        for item in decoded:
            alternatives: dict[str, str] = {}
            for language, language_segments in candidates.items():
                match = max(
                    language_segments,
                    key=lambda candidate: self._overlap(item, candidate),
                    default=None,
                )
                if match and self._overlap(item, match) > 0:
                    alternatives[language] = match["text"]
            selected_text = item["text"]
            selected_language = detected_language if self._language is None else self._language
            if self._rerank_mode == "confidence" and alternatives:
                options = [(selected_language, item["text"], item["score"])]
                for language, text in alternatives.items():
                    match = max(candidates[language], key=lambda candidate: self._overlap(item, candidate), default=None)
                    options.append((language, text, match["score"] if match else -10.0))
                selected_language, selected_text, _ = max(options, key=lambda option: option[2])
            caption = Caption(
                start=item["start"],
                end=item["end"],
                text=selected_text,
                language=selected_language,
                alternatives=alternatives,
            )
            result.append(caption)
            if progress:
                progress(caption.end, duration)
        return result

    @staticmethod
    def _overlap(left: dict, right: dict) -> float:
        return max(0.0, min(left["end"], right["end"]) - max(left["start"], right["start"]))
