from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


def _as_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def _as_float(value: str | None, default: float, minimum: float) -> float:
    try:
        return max(float(value or default), minimum)
    except (TypeError, ValueError):
        return default


def _as_int(value: str | None, default: int, minimum: int) -> int:
    try:
        return max(int(value or default), minimum)
    except (TypeError, ValueError):
        return default


def _as_language(value: str | None) -> str | None:
    """Return a Whisper language code, or None for automatic detection."""
    normalized = (value or "auto").strip().lower()
    if not normalized or normalized == "auto":
        return None
    if normalized in {"zh", "en", "ja", "ko", "yue", "fr", "de", "es", "ru"}:
        return normalized
    return None


def _as_languages(value: str | None) -> tuple[str, ...]:
    values = []
    for item in (value or "").split(","):
        language = _as_language(item)
        if language and language not in values:
            values.append(language)
    return tuple(values)


@dataclass(frozen=True, slots=True)
class Settings:
    llm_api_key: str | None
    llm_base_url: str | None
    llm_model: str
    bili_sessdata: str | None
    bili_bili_jct: str | None
    bili_buvid3: str | None
    asr_enabled: bool
    asr_model: str
    asr_device: str
    asr_compute_type: str
    asr_cache_dir: Path
    asr_language: str | None = None
    asr_candidate_languages: tuple[str, ...] = ()
    asr_rerank_mode: str = "none"
    llm_timeout_seconds: float = 90.0
    max_transcript_chars: int = 24000
    multimodal_enabled: bool = False
    multimodal_max_frames: int = 6
    media_cache_dir: Path = Path(".bili-agent/media")
    vision_model: str | None = None
    max_concurrent_analyses: int = 2
    max_question_chars: int = 4000
    max_video_input_chars: int = 500
    max_cover_bytes: int = 5 * 1024 * 1024
    web_auth_token: str | None = None
    session_ttl_seconds: int = 24 * 60 * 60
    max_sessions: int = 20
    job_ttl_seconds: int = 24 * 60 * 60
    max_jobs: int = 100
    max_history_turns: int = 24
    storage_db_path: Path = Path(".bili-agent/bili-agent.sqlite3")
    caption_timeout_seconds: float = 20.0
    max_concurrent_caption_fetches: int = 6
    asr_timeout_seconds: float = 900.0
    asr_beam_size: int = 1
    asr_best_of: int = 1
    asr_audio_format: str = "worstaudio/bestaudio"

    @classmethod
    def from_env(cls, env_file: str | Path | None = None) -> "Settings":
        load_dotenv(dotenv_path=env_file, override=False)
        return cls(
            llm_api_key=os.getenv("LLM_API_KEY") or None,
            llm_base_url=os.getenv("LLM_BASE_URL") or None,
            llm_model=os.getenv("LLM_MODEL", "gpt-4o-mini"),
            bili_sessdata=os.getenv("BILI_SESSDATA") or None,
            bili_bili_jct=os.getenv("BILI_BILI_JCT") or None,
            bili_buvid3=os.getenv("BILI_BUVID3") or None,
            asr_enabled=_as_bool(os.getenv("ASR_ENABLED"), True),
            asr_model=os.getenv("ASR_MODEL", "base"),
            asr_device=os.getenv("ASR_DEVICE", "cpu"),
            asr_compute_type=os.getenv("ASR_COMPUTE_TYPE", "int8"),
            asr_cache_dir=Path(os.getenv("ASR_CACHE_DIR", ".bili-agent/audio")),
            asr_language=_as_language(os.getenv("ASR_LANGUAGE")),
            asr_candidate_languages=_as_languages(os.getenv("ASR_CANDIDATE_LANGUAGES")),
            asr_rerank_mode=os.getenv("ASR_RERANK_MODE", "none").strip().lower()
            if os.getenv("ASR_RERANK_MODE", "none").strip().lower() in {"none", "confidence", "llm"}
            else "none",
            llm_timeout_seconds=_as_float(os.getenv("LLM_TIMEOUT_SECONDS"), 90.0, 1.0),
            max_transcript_chars=_as_int(os.getenv("MAX_TRANSCRIPT_CHARS"), 24000, 1000),
            multimodal_enabled=_as_bool(os.getenv("MULTIMODAL_ENABLED")),
            multimodal_max_frames=_as_int(os.getenv("MULTIMODAL_MAX_FRAMES"), 6, 1),
            media_cache_dir=Path(os.getenv("MEDIA_CACHE_DIR", ".bili-agent/media")),
            vision_model=os.getenv("VISION_MODEL") or None,
            max_concurrent_analyses=_as_int(os.getenv("MAX_CONCURRENT_ANALYSES"), 2, 1),
            max_question_chars=_as_int(os.getenv("MAX_QUESTION_CHARS"), 4000, 100),
            max_video_input_chars=_as_int(os.getenv("MAX_VIDEO_INPUT_CHARS"), 500, 32),
            max_cover_bytes=_as_int(os.getenv("MAX_COVER_BYTES"), 5 * 1024 * 1024, 64 * 1024),
            web_auth_token=os.getenv("WEB_AUTH_TOKEN") or None,
            session_ttl_seconds=_as_int(os.getenv("SESSION_TTL_SECONDS"), 24 * 60 * 60, 60),
            max_sessions=_as_int(os.getenv("MAX_SESSIONS"), 20, 1),
            job_ttl_seconds=_as_int(os.getenv("JOB_TTL_SECONDS"), 24 * 60 * 60, 60),
            max_jobs=_as_int(os.getenv("MAX_JOBS"), 100, 10),
            max_history_turns=_as_int(os.getenv("MAX_HISTORY_TURNS"), 24, 2),
            storage_db_path=Path(os.getenv("STORAGE_DB_PATH", ".bili-agent/bili-agent.sqlite3")),
            caption_timeout_seconds=_as_float(os.getenv("CAPTION_TIMEOUT_SECONDS"), 20.0, 3.0),
            max_concurrent_caption_fetches=_as_int(os.getenv("MAX_CONCURRENT_CAPTION_FETCHES"), 6, 1),
            asr_timeout_seconds=_as_float(os.getenv("ASR_TIMEOUT_SECONDS"), 900.0, 30.0),
            asr_beam_size=_as_int(os.getenv("ASR_BEAM_SIZE"), 1, 1),
            asr_best_of=_as_int(os.getenv("ASR_BEST_OF"), 1, 1),
            asr_audio_format=os.getenv("ASR_AUDIO_FORMAT", "worstaudio/bestaudio"),
        )

    @property
    def bili_cookies(self) -> dict[str, str]:
        return {
            key: value
            for key, value in {
                "SESSDATA": self.bili_sessdata,
                "bili_jct": self.bili_bili_jct,
                "buvid3": self.bili_buvid3,
            }.items()
            if value
        }
