from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from typing import Any

import httpx

from .asr import ASRTranscriber, AudioDownloadError, AudioDownloader
from .config import Settings
from .models import Caption, PageInfo, PageTranscript, VideoIdentifier, VideoMetadata

LOGGER = logging.getLogger(__name__)


def _payload(value: Any) -> Any:
    if isinstance(value, dict) and "data" in value:
        return value["data"]
    return value


def _first(value: Any, *keys: str, default: Any = None) -> Any:
    if not isinstance(value, dict):
        return default
    for key in keys:
        if value.get(key) is not None:
            return value[key]
    return default


class BilibiliClient:
    def __init__(self, settings: Settings):
        self.settings = settings

    async def fetch_metadata(self, identifier: VideoIdentifier) -> tuple[VideoMetadata, Any]:
        try:
            from bilibili_api import Credential, video
        except ImportError as exc:
            raise RuntimeError("缺少 bilibili-api-python，请先执行 pip install -e .") from exc

        credential = Credential(**self.settings.bili_cookies) if self.settings.bili_cookies else Credential()
        bili_video = video.Video(bvid=identifier.value, credential=credential) if identifier.kind == "bvid" else video.Video(aid=int(identifier.value), credential=credential)
        raw_info = _payload(await bili_video.get_info())
        try:
            raw_pages = _payload(await bili_video.get_pages())
        except Exception:
            LOGGER.exception("获取分P列表失败，将尝试使用视频详情中的 pages 字段。")
            raw_pages = raw_info.get("pages", []) if isinstance(raw_info, dict) else []

        page_values = raw_pages.get("pages", raw_pages) if isinstance(raw_pages, dict) else raw_pages
        if not page_values:
            page_values = raw_info.get("pages", [])
        if not page_values and isinstance(raw_info, dict) and raw_info.get("cid"):
            page_values = [{
                "page": 1,
                "cid": raw_info["cid"],
                "part": raw_info.get("title") or "第 1 P",
                "duration": raw_info.get("duration", 0),
            }]
        pages = [
            PageInfo(
                page_index=int(_first(item, "page", default=index)) - 1,
                cid=int(item["cid"]),
                title=_first(item, "part", "title", default=f"第 {index + 1} P") or f"第 {index + 1} P",
                duration_seconds=int(_first(item, "duration", default=0) or 0),
            )
            for index, item in enumerate(page_values or [])
        ]
        pages = [page.model_copy(update={"page_index": max(page.page_index, 0)}) for page in pages]
        owner = raw_info.get("owner", {}) if isinstance(raw_info, dict) else {}
        pubdate = _first(raw_info, "pubdate")
        published = datetime.fromtimestamp(int(pubdate), tz=timezone.utc) if pubdate else None
        metadata = VideoMetadata(
            bvid=str(_first(raw_info, "bvid", default=getattr(bili_video, "get_bvid", lambda: "")())),
            aid=int(_first(raw_info, "aid", default=getattr(bili_video, "get_aid", lambda: 0)())),
            title=str(_first(raw_info, "title", default=identifier.value)),
            author=str(_first(owner, "name", default="未知")),
            pubdate=published,
            duration_seconds=int(_first(raw_info, "duration", default=sum(page.duration_seconds for page in pages)) or 0),
            description=str(_first(raw_info, "desc", "description", default="") or ""),
            pic=str(_first(raw_info, "pic", default="") or "") or None,
            pages=pages,
            url=identifier.canonical_url,
        )
        return metadata, bili_video

    async def fetch_transcripts(
        self,
        metadata: VideoMetadata,
        bili_video: Any,
        asr_enabled: bool,
        asr_transcriber: ASRTranscriber | None = None,
        audio_downloader: AudioDownloader | None = None,
        asr_notice: str | None = None,
        progress: Callable[[str, int, int, str], Awaitable[None]] | None = None,
    ) -> list[PageTranscript]:
        pages = list(metadata.pages)
        if not pages:
            return []

        semaphore = asyncio.Semaphore(self.settings.max_concurrent_caption_fetches)
        completed = 0
        completed_lock = asyncio.Lock()

        async def await_with_heartbeat(
            factory: Callable[[], Awaitable[Any]],
            label: str,
            position: int,
        ) -> Any:
            """Keep long yt-dlp/Whisper operations visible and cancellable."""
            task = asyncio.create_task(factory())
            started = asyncio.get_running_loop().time()
            try:
                while True:
                    try:
                        return await asyncio.wait_for(asyncio.shield(task), timeout=5.0)
                    except asyncio.TimeoutError:
                        elapsed = int(asyncio.get_running_loop().time() - started)
                        if progress:
                            try:
                                await progress(
                                    "asr_heartbeat",
                                    position - 0.5,
                                    len(pages),
                                    f"{label}（已运行 {elapsed} 秒）",
                                )
                            except Exception:
                                LOGGER.debug("ASR 心跳进度更新失败。", exc_info=True)
            finally:
                if not task.done():
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)

        async def fetch_cc(page: PageInfo) -> list[Caption]:
            nonlocal completed
            async with semaphore:
                try:
                    return await asyncio.wait_for(
                        self._fetch_cc(metadata, bili_video, page),
                        timeout=self.settings.caption_timeout_seconds,
                    )
                except asyncio.TimeoutError:
                    LOGGER.warning("第 %s P 获取 CC 字幕超时（%.1f 秒）。", page.page_index + 1, self.settings.caption_timeout_seconds)
                    return []
                except Exception:
                    LOGGER.warning("第 %s P 获取 CC 字幕失败。", page.page_index + 1, exc_info=True)
                    return []
                finally:
                    async with completed_lock:
                        completed += 1
                        if progress:
                            await progress(
                                "cc",
                                completed,
                                len(pages),
                                f"正在获取 CC 字幕（{completed}/{len(pages)}）",
                            )

        cc_segments = await asyncio.gather(*(fetch_cc(page) for page in pages))
        transcripts: list[PageTranscript] = []
        for position, (page, segments) in enumerate(zip(pages, cc_segments), start=1):
            if segments:
                transcripts.append(PageTranscript(page=page, source="cc", segments=segments))
                continue

            page_notice = None
            if asr_enabled and asr_transcriber and audio_downloader:
                if progress:
                    await progress(
                        "asr",
                        position - 1,
                        len(pages),
                        f"正在准备第 {page.page_index + 1} P 的音频转写",
                    )
                try:
                    page_url = f"{metadata.url}{'&' if '?' in metadata.url else '?'}p={page.page_index + 1}"
                    audio = await asyncio.wait_for(
                        await_with_heartbeat(
                            lambda: audio_downloader.download(page_url, page),
                            f"正在下载第 {page.page_index + 1} P 的音频",
                            position - 1,
                        ),
                        timeout=self.settings.asr_timeout_seconds,
                    )
                    if progress:
                        await progress(
                            "asr",
                            position - 1,
                            len(pages),
                            f"正在转写第 {page.page_index + 1} P 的音频",
                        )

                    async def transcribe_page() -> list[Caption]:
                        detailed_transcribe = getattr(asr_transcriber, "transcribe_with_progress", None)
                        if not callable(detailed_transcribe) or not progress:
                            return await asr_transcriber.transcribe(audio)

                        loop = asyncio.get_running_loop()
                        progress_tasks: set[asyncio.Task] = set()

                        def schedule_segment_progress(end: float, duration: float) -> None:
                            fraction = min(max(end / duration, 0.0), 1.0) if duration > 0 else 0.0
                            seconds = max(int(end), 0)
                            total_seconds = max(int(duration), 0)

                            def schedule() -> None:
                                task = asyncio.create_task(
                                    progress(
                                        "asr_progress",
                                        fraction,
                                        1,
                                        f"正在转写第 {page.page_index + 1} P 的音频（已处理约 {seconds}/{total_seconds} 秒）",
                                    )
                                )
                                progress_tasks.add(task)
                                task.add_done_callback(progress_tasks.discard)

                            loop.call_soon_threadsafe(schedule)

                        result = await detailed_transcribe(audio, schedule_segment_progress)
                        if progress_tasks:
                            await asyncio.gather(*progress_tasks, return_exceptions=True)
                        return result

                    asr_segments = await asyncio.wait_for(
                        await_with_heartbeat(
                            transcribe_page,
                            f"正在转写第 {page.page_index + 1} P 的音频",
                            position - 1,
                        ),
                        timeout=self.settings.asr_timeout_seconds,
                    )
                    if asr_segments:
                        transcripts.append(PageTranscript(page=page, source="asr", segments=asr_segments))
                    else:
                        page_notice = "音频已获取，但 ASR 未检测到可识别语音（可能是音乐、环境声或静音）。"
                        transcripts.append(PageTranscript(page=page, source="none", notice=page_notice))
                    continue
                except asyncio.TimeoutError:
                    page_notice = "音频转写超时，已跳过该分P；可降低 ASR 模型或改用 GPU。"
                    LOGGER.warning(
                        "第 %s P 的 ASR 超时（%.1f 秒），跳过并继续分析。",
                        page.page_index + 1,
                        self.settings.asr_timeout_seconds,
                    )
                except AudioDownloadError as exc:
                    page_notice = str(exc)
                    LOGGER.warning("第 %s P 音频下载未完成: %s", page.page_index + 1, exc)
                except Exception:
                    page_notice = "音频下载或转写失败，已保留元数据降级结果；请查看服务日志。"
                    LOGGER.exception("第 %s P 的 ASR 失败，保留无字幕状态。", page.page_index + 1)
                finally:
                    if progress:
                        await progress(
                            "asr",
                            position,
                            len(pages),
                            f"第 {page.page_index + 1} P 的音频处理完成",
                        )

            notice = page_notice or asr_notice or "该分P没有可用 CC 字幕；ASR 默认未启用。"
            transcripts.append(PageTranscript(page=page, source="none", notice=notice))
            LOGGER.warning("第 %s P 没有可用 CC 字幕。", page.page_index + 1)
        return transcripts

    async def _fetch_cc(self, metadata: VideoMetadata, bili_video: Any, page: PageInfo) -> list[Caption]:
        # Newer/forked releases may expose a player helper; use it when present.
        for method_name in ("get_player_info", "get_player_v2", "get_subtitle"):
            method = getattr(bili_video, method_name, None)
            if method is None:
                continue
            try:
                raw = await method(cid=page.cid, page_index=page.page_index)
                segments = await self._subtitle_segments_from_player(raw)
                if segments:
                    return segments
            except (TypeError, AttributeError, KeyError):
                continue
            except Exception:
                LOGGER.debug("bilibili-api-python 的 %s 字幕 helper 调用失败。", method_name, exc_info=True)

        params = {"bvid": metadata.bvid, "aid": metadata.aid, "cid": page.cid}
        headers = {"Referer": metadata.url, "User-Agent": "Mozilla/5.0 bili-agent/0.1"}
        try:
            async with httpx.AsyncClient(timeout=20, headers=headers, cookies=self.settings.bili_cookies) as client:
                response = await client.get("https://api.bilibili.com/x/player/v2", params=params)
                response.raise_for_status()
                raw = response.json()
            return await self._subtitle_segments_from_player(raw)
        except (httpx.HTTPError, ValueError) as exc:
            LOGGER.warning("第 %s P 获取 CC 字幕失败: %s", page.page_index + 1, exc)
            return []

    async def _subtitle_segments_from_player(self, raw: Any) -> list[Caption]:
        data = _payload(raw)
        if isinstance(data, list):
            return self._parse_caption_entries(data)
        if isinstance(data, dict) and "body" in data:
            return self._parse_caption_entries(data["body"])
        subtitle = data.get("subtitle", {}) if isinstance(data, dict) else {}
        refs = subtitle.get("subtitles", []) if isinstance(subtitle, dict) else []
        if isinstance(refs, dict):
            refs = [refs]
        for ref in refs:
            url = ref.get("subtitle_url") or ref.get("url") if isinstance(ref, dict) else None
            if not url:
                continue
            if str(url).startswith("//"):
                url = "https:" + str(url)
            try:
                headers = {"User-Agent": "Mozilla/5.0 bili-agent/0.1"}
                async with httpx.AsyncClient(
                    timeout=20, headers=headers, cookies=self.settings.bili_cookies
                ) as client:
                    response = await client.get(str(url))
                    response.raise_for_status()
                    body = response.json()
                entries = body.get("body", body) if isinstance(body, dict) else body
                return self._parse_caption_entries(entries)
            except (httpx.HTTPError, ValueError, TypeError) as exc:
                LOGGER.warning("下载字幕资源失败: %s", exc)
        return []

    @staticmethod
    def _parse_caption_entries(entries: Any) -> list[Caption]:
        if not isinstance(entries, list):
            return []
        result: list[Caption] = []
        for item in entries:
            if not isinstance(item, dict):
                continue
            text = str(_first(item, "content", "text", default="") or "").strip()
            if not text:
                continue
            start = float(_first(item, "from", "start", default=0) or 0)
            end = float(_first(item, "to", "end", default=start) or start)
            result.append(Caption(start=max(start, 0), end=max(end, start), text=text))
        return result
