from __future__ import annotations

import importlib.util
import shutil
import sys
from pathlib import Path
from typing import Any

from . import __version__
from .config import Settings


def _module_available(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ModuleNotFoundError, ValueError):
        return False


def _directory_writable(path: Path) -> bool:
    try:
        path = path.expanduser()
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".bili-agent-write-check"
        probe.touch(exist_ok=False)
        probe.unlink()
        return True
    except (OSError, ValueError):
        return False


def runtime_diagnostics(settings: Settings) -> dict[str, Any]:
    """Return safe, non-secret diagnostics for health checks and support logs."""
    asr_available = _module_available("faster_whisper")
    yt_dlp_available = _module_available("yt_dlp")
    ffmpeg_available = shutil.which("ffmpeg") is not None
    vision_available = _module_available("av") and _module_available("pytesseract")
    storage_writable = _directory_writable(settings.storage_db_path.parent)

    warnings: list[str] = []
    if not settings.llm_api_key:
        warnings.append("未配置 LLM_API_KEY，将使用降级总结和证据问答。")
    if settings.asr_enabled and not asr_available:
        warnings.append("ASR 已开启但未安装 faster-whisper。")
    if settings.asr_enabled and not ffmpeg_available:
        warnings.append("ASR 需要 ffmpeg，但当前 PATH 中未找到。")
    if settings.multimodal_enabled and not vision_available:
        warnings.append("多模态已开启但缺少 av 或 pytesseract。")
    if not storage_writable:
        warnings.append("持久化目录不可写，会影响会话和任务恢复。")

    return {
        "status": "ok",
        "ready": storage_writable,
        "version": __version__,
        "python": ".".join(str(part) for part in sys.version_info[:3]),
        "llm_configured": bool(settings.llm_api_key),
        "auth_required": bool(settings.web_auth_token),
        "capabilities": {
            "asr": asr_available,
            "yt_dlp": yt_dlp_available,
            "ffmpeg": ffmpeg_available,
            "vision": vision_available,
        },
        "storage": {"writable": storage_writable},
        "warnings": warnings,
    }
