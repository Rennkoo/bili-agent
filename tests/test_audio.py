import asyncio

from bili_agent.asr import AudioDownloadError, AudioDownloader
from bili_agent.bilibili_client import BilibiliClient
from bili_agent.config import Settings
from bili_agent.models import PageInfo, VideoMetadata


def _settings() -> Settings:
    return Settings(
        llm_api_key=None,
        llm_base_url=None,
        llm_model="gpt-4o-mini",
        bili_sessdata=None,
        bili_bili_jct=None,
        bili_buvid3=None,
        asr_enabled=False,
        asr_model="base",
        asr_device="cpu",
        asr_compute_type="int8",
        asr_cache_dir=".cache",
    )


def test_audio_error_classification_reports_bilibili_restriction():
    error = AudioDownloader.classify_error(
        RuntimeError("Only preview format is available, you have to become a premium member")
    )
    assert isinstance(error, AudioDownloadError)
    assert error.code == "restricted"
    assert "会员权限" in str(error)


def test_audio_downloader_keeps_configured_selector_first():
    downloader = AudioDownloader(".cache", "custom-format")
    assert downloader._format_selectors()[0] == "custom-format"
    assert len(downloader._format_selectors()) == 3


def test_no_asr_path_keeps_a_clear_missing_caption_notice():
    client = BilibiliClient(_settings())
    page = PageInfo(page_index=0, cid=123, title="无字幕")
    metadata = VideoMetadata(
        bvid="BV1xx411c7mD",
        aid=170001,
        title="测试视频",
        pages=[page],
        duration_seconds=30,
        url="https://www.bilibili.com/video/BV1xx411c7mD",
    )

    async def empty_cc(*args, **kwargs):
        return []

    client._fetch_cc = empty_cc
    transcripts = asyncio.run(client.fetch_transcripts(metadata, object(), False))
    assert transcripts[0].source == "none"
    assert "没有可用 CC 字幕" in transcripts[0].notice
