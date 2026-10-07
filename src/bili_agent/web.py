from __future__ import annotations

import asyncio
import hmac
import json
import logging
import mimetypes
import threading
import time
import uuid
import webbrowser
from dataclasses import replace
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urljoin, urlparse

from .agent import BiliAgent
from .asr import AudioDownloadError, AudioDownloader
from .config import Settings
from .diagnostics import runtime_diagnostics
from .infographic import render_infographic
from .markdown import render_markdown
from .models import AnalysisResult
from .parser import InputParseError, parse_video_input
from .skills import skill_label
from .storage import SQLiteStore

LOGGER = logging.getLogger(__name__)
STATIC_DIR = Path(__file__).with_name("static")
ENV_PATH = Path.cwd() / ".env"
SETTINGS_LOCK = threading.Lock()


class SessionStore:
    def __init__(
        self,
        ttl_seconds: int = 24 * 60 * 60,
        max_items: int = 20,
        max_history_turns: int = 24,
        storage: SQLiteStore | None = None,
    ):
        self._items: dict[str, AnalysisResult] = {}
        self._history: dict[str, list[dict[str, str]]] = {}
        self._accessed: dict[str, float] = {}
        self._ttl_seconds = max(ttl_seconds, 60)
        self._max_items = max(max_items, 1)
        self._max_history_turns = max(max_history_turns, 2)
        self._storage = storage
        self._lock = threading.Lock()
        if self._storage:
            for session_id, accessed_at, data_json, history_json in self._storage.load_sessions():
                try:
                    self._items[session_id] = AnalysisResult.model_validate(json.loads(data_json))
                    self._history[session_id] = json.loads(history_json)
                    self._accessed[session_id] = accessed_at
                except (ValueError, TypeError, json.JSONDecodeError) as exc:
                    LOGGER.warning("忽略损坏的持久化会话 %s: %s", session_id[:8], exc)
            with self._lock:
                self._purge_locked()

    def _purge_locked(self) -> None:
        cutoff = time.time() - self._ttl_seconds
        expired = [session_id for session_id, accessed in self._accessed.items() if accessed < cutoff]
        for session_id in expired:
            self._items.pop(session_id, None)
            self._history.pop(session_id, None)
            self._accessed.pop(session_id, None)
        if self._storage:
            self._storage.delete_sessions(expired)
        evicted: list[str] = []
        while len(self._items) > self._max_items:
            oldest = min(self._accessed, key=self._accessed.get)
            self._items.pop(oldest, None)
            self._history.pop(oldest, None)
            self._accessed.pop(oldest, None)
            evicted.append(oldest)
        if self._storage:
            self._storage.delete_sessions(evicted)

    def put(self, result: AnalysisResult) -> str:
        session_id = uuid.uuid4().hex
        with self._lock:
            self._purge_locked()
            self._items[session_id] = result
            self._history[session_id] = []
            self._accessed[session_id] = time.time()
            if self._storage:
                self._storage.save_session(
                    session_id,
                    self._accessed[session_id],
                    result.model_dump(mode="json"),
                    self._history[session_id],
                )
        return session_id

    def get(self, session_id: str) -> AnalysisResult | None:
        with self._lock:
            self._purge_locked()
            item = self._items.get(session_id)
            if item is not None:
                self._accessed[session_id] = time.time()
                if self._storage:
                    self._storage.save_session(
                        session_id,
                        self._accessed[session_id],
                        item.model_dump(mode="json"),
                        self._history[session_id],
                    )
            return item

    def history(self, session_id: str) -> list[dict[str, str]]:
        with self._lock:
            self._purge_locked()
            if session_id in self._items:
                self._accessed[session_id] = time.time()
            return list(self._history.get(session_id, []))

    def add_turn(self, session_id: str, role: str, content: str) -> None:
        with self._lock:
            if session_id in self._history:
                self._history[session_id].append({"role": role, "content": content})
                self._history[session_id] = self._history[session_id][-self._max_history_turns :]
                self._accessed[session_id] = time.time()
                if self._storage:
                    self._storage.save_session(
                        session_id,
                        self._accessed[session_id],
                        self._items[session_id].model_dump(mode="json"),
                        self._history[session_id],
                    )


class JobStore:
    def __init__(self, ttl_seconds: int = 24 * 60 * 60, max_items: int = 100, storage: SQLiteStore | None = None):
        self._items: dict[str, dict] = {}
        self._updated: dict[str, float] = {}
        self._ttl_seconds = max(ttl_seconds, 60)
        self._max_items = max(max_items, 10)
        self._storage = storage
        self._lock = threading.Lock()
        if self._storage:
            for job_id, updated_at, data_json in self._storage.load_jobs():
                try:
                    data = json.loads(data_json)
                    if data.get("status") in {"queued", "running"}:
                        data.update(
                            status="failed",
                            stage="failed",
                            progress=100,
                            message="服务重启，任务已中断。",
                        )
                        if self._storage:
                            self._storage.save_job(job_id, updated_at, data)
                    self._items[job_id] = data
                    self._updated[job_id] = updated_at
                except (ValueError, TypeError, json.JSONDecodeError) as exc:
                    LOGGER.warning("忽略损坏的持久化任务 %s: %s", job_id[:8], exc)
            with self._lock:
                self._purge_locked()

    def _purge_locked(self) -> None:
        cutoff = time.time() - self._ttl_seconds
        terminal = {
            job_id
            for job_id, item in self._items.items()
            if item.get("status") in {"completed", "failed"} and self._updated.get(job_id, 0) < cutoff
        }
        for job_id in terminal:
            self._items.pop(job_id, None)
            self._updated.pop(job_id, None)
        if self._storage:
            self._storage.delete_jobs(list(terminal))
        evicted: list[str] = []
        while len(self._items) > self._max_items:
            finished = [
                job_id for job_id, item in self._items.items() if item.get("status") in {"completed", "failed"}
            ]
            if not finished:
                break
            oldest = min(finished, key=self._updated.get)
            self._items.pop(oldest, None)
            self._updated.pop(oldest, None)
            evicted.append(oldest)
        if self._storage:
            self._storage.delete_jobs(evicted)

    def create(self) -> str:
        job_id = uuid.uuid4().hex
        with self._lock:
            self._purge_locked()
            self._items[job_id] = {
                "job_id": job_id,
                "status": "queued",
                "stage": "queued",
                "progress": 0,
                "message": "等待开始",
            }
            self._updated[job_id] = time.time()
            if self._storage:
                self._storage.save_job(job_id, self._updated[job_id], self._items[job_id])
        return job_id

    def update(self, job_id: str, **values) -> None:
        with self._lock:
            self._purge_locked()
            if job_id in self._items:
                self._items[job_id].update(values)
                self._updated[job_id] = time.time()
                if self._storage:
                    self._storage.save_job(job_id, self._updated[job_id], self._items[job_id])

    def get(self, job_id: str) -> dict | None:
        with self._lock:
            self._purge_locked()
            item = self._items.get(job_id)
            return dict(item) if item else None


def _json_default(value):
    return value.model_dump(mode="json") if hasattr(value, "model_dump") else str(value)


def _send_json(
    handler: BaseHTTPRequestHandler,
    payload: dict,
    status: int = 200,
    headers: dict[str, str] | None = None,
) -> None:
    body = json.dumps(payload, ensure_ascii=False, default=_json_default).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Cache-Control", "no-store")
    for key, value in (headers or {}).items():
        handler.send_header(key, value)
    handler.end_headers()
    handler.wfile.write(body)


async def _run_with_cleanup(coro):
    """Run one request and close bilibili-api's loop-bound client before loop shutdown."""
    try:
        return await coro
    finally:
        try:
            from bilibili_api.utils.network import get_client

            await get_client().close()
        except Exception:
            # The client may not have been initialized for settings/ask calls.
            LOGGER.debug("关闭 bilibili-api HTTP 客户端时跳过异常。", exc_info=True)


def _run(coro):
    return asyncio.run(_run_with_cleanup(coro))


def _mask_secret(value: str | None) -> str:
    if not value:
        return ""
    if len(value) <= 8:
        return "••••••••"
    return f"{value[:4]}••••{value[-4:]}"


def _write_env_values(values: dict[str, str]) -> None:
    """Update only the supported local settings while preserving the .env file."""
    lines = ENV_PATH.read_text(encoding="utf-8").splitlines() if ENV_PATH.exists() else []
    seen: set[str] = set()
    output: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in line:
            output.append(line)
            continue
        key = line.split("=", 1)[0].strip()
        if key in values:
            output.append(f"{key}={values[key]}")
            seen.add(key)
        else:
            output.append(line)
    for key, value in values.items():
        if key not in seen:
            output.append(f"{key}={value}")
    ENV_PATH.write_text("\n".join(output).rstrip() + "\n", encoding="utf-8")


class WebHandler(BaseHTTPRequestHandler):
    store = SessionStore()
    jobs = JobStore()
    storage: SQLiteStore | None = None
    settings: Settings
    analysis_slots = threading.BoundedSemaphore(2)

    def log_message(self, format: str, *args) -> None:
        LOGGER.info("web %s", format % args)

    def _is_authorized(self) -> bool:
        expected = self.settings.web_auth_token
        if not expected:
            return True
        header = self.headers.get("Authorization", "")
        scheme, _, supplied = header.partition(" ")
        return scheme.lower() == "bearer" and bool(supplied) and hmac.compare_digest(supplied, expected)

    def _require_auth(self) -> bool:
        if self._is_authorized():
            return True
        _send_json(
            self,
            {"error": "需要有效的面板访问 Token。"},
            HTTPStatus.UNAUTHORIZED,
            headers={"WWW-Authenticate": "Bearer"},
        )
        return False

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/health":
            _send_json(self, runtime_diagnostics(self.settings))
            return
        if parsed.path.startswith("/api/") and parsed.path != "/api/cover" and not self._require_auth():
            return
        if parsed.path == "/api/settings":
            _send_json(
                self,
                {
                    "llm_configured": bool(self.settings.llm_api_key),
                    "llm_api_key_masked": _mask_secret(self.settings.llm_api_key),
                    "llm_base_url": self.settings.llm_base_url or "https://api.openai.com/v1",
                    "llm_model": self.settings.llm_model,
                },
            )
            return
        if parsed.path == "/api/cover":
            self._serve_cover(parsed)
            return
        if parsed.path == "/api/audio":
            self._serve_audio(parsed)
            return
        if parsed.path.startswith("/api/jobs/"):
            job_id = parsed.path.rsplit("/", 1)[-1]
            job = self.jobs.get(job_id)
            _send_json(self, job or {"error": "任务不存在。"}, HTTPStatus.OK if job else HTTPStatus.NOT_FOUND)
            return
        self._serve_static(parsed.path)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path.startswith("/api/") and not self._require_auth():
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length > 2_000_000:
                raise ValueError("请求体过大。")
            raw_body = self.rfile.read(length)
            payload = json.loads(raw_body or b"{}")
            if not isinstance(payload, dict):
                raise ValueError("请求体必须是 JSON 对象。")
            if parsed.path == "/api/analyze":
                self._analyze(payload)
                return
            if parsed.path == "/api/inspect":
                self._inspect(payload)
                return
            if parsed.path == "/api/settings":
                self._save_settings(payload)
                return
            if parsed.path == "/api/ask":
                self._ask(payload)
                return
            if parsed.path == "/api/infographic":
                self._infographic(payload)
                return
            _send_json(self, {"error": "接口不存在。"}, HTTPStatus.NOT_FOUND)
        except (ValueError, json.JSONDecodeError) as exc:
            _send_json(self, {"error": str(exc) or "请求格式错误。"}, HTTPStatus.BAD_REQUEST)
        except Exception as exc:
            LOGGER.exception("Web 请求失败。")
            _send_json(self, {"error": str(exc) or "服务处理失败。"}, HTTPStatus.INTERNAL_SERVER_ERROR)

    def _analyze(self, payload: dict) -> None:
        video = str(payload.get("video", "")).strip()
        if not video:
            raise ValueError("请输入 Bilibili 视频链接、BV 号或 av 号。")
        if len(video) > self.settings.max_video_input_chars:
            raise ValueError(f"视频输入过长，最多允许 {self.settings.max_video_input_chars} 个字符。")
        try:
            parse_video_input(video)
        except InputParseError as exc:
            raise ValueError(str(exc)) from exc
        page_indices = payload.get("page_indices")
        if page_indices is not None:
            if not isinstance(page_indices, list) or not page_indices:
                raise ValueError("至少选择一个分P。")
            try:
                page_indices = sorted({int(value) for value in page_indices})
            except (TypeError, ValueError) as exc:
                raise ValueError("分P选择无效。") from exc
            if any(value < 0 for value in page_indices):
                raise ValueError("分P选择无效。")
        # The web workflow is designed for video understanding: transcribe only
        # when CC subtitles are unavailable, while keeping the CLI conservative.
        enable_asr = bool(payload.get("enable_asr", True))
        enable_multimodal = bool(payload.get("enable_multimodal", False))
        if not self.analysis_slots.acquire(blocking=False):
            _send_json(self, {"error": "当前分析任务较多，请稍后再试。"}, HTTPStatus.TOO_MANY_REQUESTS)
            return
        job_id = self.jobs.create()
        thread = threading.Thread(
            target=self._run_analysis_job,
            args=(job_id, video, page_indices, enable_asr, enable_multimodal),
            daemon=True,
            name=f"bili-agent-{job_id[:8]}",
        )
        try:
            thread.start()
        except Exception:
            self.analysis_slots.release()
            raise
        _send_json(self, {"job_id": job_id}, HTTPStatus.ACCEPTED)

    def _inspect(self, payload: dict) -> None:
        video = str(payload.get("video", "")).strip()
        if not video:
            raise ValueError("请输入 Bilibili 视频链接、BV 号或 av 号。")
        if len(video) > self.settings.max_video_input_chars:
            raise ValueError(f"视频输入过长，最多允许 {self.settings.max_video_input_chars} 个字符。")
        try:
            identifier = parse_video_input(video)
        except InputParseError as exc:
            raise ValueError(str(exc)) from exc
        agent = BiliAgent(self.settings)
        metadata, _ = _run(agent.bilibili.fetch_metadata(identifier))
        _send_json(self, {"metadata": metadata.model_dump(mode="json")})

    def _save_settings(self, payload: dict) -> None:
        base_url = str(payload.get("llm_base_url", "")).strip()
        model = str(payload.get("llm_model", "")).strip()
        api_key = payload.get("llm_api_key")
        clear_key = bool(payload.get("clear_api_key", False))
        if not base_url:
            base_url = "https://api.openai.com/v1"
        if any(char in base_url for char in "\r\n"):
            raise ValueError("Base URL 不能包含换行符。")
        parsed_base_url = urlparse(base_url)
        if parsed_base_url.scheme not in {"http", "https"} or not parsed_base_url.netloc:
            raise ValueError("Base URL 必须是有效的 http(s) 地址。")
        if not model:
            raise ValueError("模型名不能为空。")
        if any(char in model for char in "\r\n"):
            raise ValueError("模型名不能包含换行符。")
        if api_key is not None and not isinstance(api_key, str):
            raise ValueError("API Key 必须是文本。")
        if isinstance(api_key, str) and any(char in api_key for char in "\r\n"):
            raise ValueError("API Key 不能包含换行符。")
        with SETTINGS_LOCK:
            next_key = None if clear_key else (api_key.strip() if isinstance(api_key, str) and api_key.strip() else self.settings.llm_api_key)
            next_settings = replace(
                self.settings,
                llm_api_key=next_key,
                llm_base_url=base_url,
                llm_model=model,
            )
            WebHandler.settings = next_settings
            self.settings = next_settings
            _write_env_values(
                {
                    "LLM_API_KEY": next_key or "",
                    "LLM_BASE_URL": base_url,
                    "LLM_MODEL": model,
                }
            )
        _send_json(
            self,
            {
                "saved": True,
                "llm_configured": bool(self.settings.llm_api_key),
                "llm_api_key_masked": _mask_secret(self.settings.llm_api_key),
                "llm_base_url": self.settings.llm_base_url,
                "llm_model": self.settings.llm_model,
            },
        )

    def _run_analysis_job(
        self,
        job_id: str,
        video: str,
        page_indices: list[int] | None,
        enable_asr: bool,
        enable_multimodal: bool,
    ) -> None:
        async def progress(stage: str, percent: int, message: str) -> None:
            self.jobs.update(job_id, status="running", stage=stage, progress=percent, message=message)

        try:
            result = _run(
                BiliAgent(self.settings).analyze(
                    video,
                    page_indices=page_indices,
                    enable_asr=enable_asr,
                    enable_multimodal=enable_multimodal,
                    progress=progress,
                )
            )
            session_id = self.store.put(result)
            self.jobs.update(
                job_id,
                status="completed",
                stage="complete",
                progress=100,
                message="分析完成",
                session_id=session_id,
                result=result.model_dump(mode="json"),
                markdown=render_markdown(result),
            )
        except Exception as exc:
            LOGGER.exception("后台分析任务失败。")
            self.jobs.update(job_id, status="failed", stage="failed", progress=100, message=str(exc))
        finally:
            self.analysis_slots.release()

    def _ask(self, payload: dict) -> None:
        session_id = str(payload.get("session_id", "")).strip()
        question = str(payload.get("question", "")).strip()
        result = self.store.get(session_id)
        if not result:
            raise ValueError("分析会话不存在，请先分析一个视频。")
        if not question:
            raise ValueError("问题不能为空。")
        if len(question) > self.settings.max_question_chars:
            raise ValueError(f"问题过长，最多允许 {self.settings.max_question_chars} 个字符。")
        history = self.store.history(session_id)
        answer = _run(BiliAgent(self.settings).ask(result, question, history=history))
        self.store.add_turn(session_id, "user", question)
        self.store.add_turn(session_id, "assistant", answer.answer)
        LOGGER.info(
            "问答技能=%s session=%s sources=%s",
            skill_label(answer.skill),
            session_id[:8],
            len(answer.sources),
        )
        _send_json(self, {"answer": answer.model_dump(mode="json")})

    def _infographic(self, payload: dict) -> None:
        session_id = str(payload.get("session_id", "")).strip()
        result = self.store.get(session_id)
        if not result:
            raise ValueError("分析会话不存在，请先分析一个视频。")
        svg = render_infographic(result)
        _send_json(
            self,
            {
                "svg": svg,
                "filename": f"{result.metadata.title or 'bili-infographic'}.svg",
            },
        )

    def _serve_cover(self, parsed) -> None:
        remote_url = parse_qs(parsed.query).get("url", [""])[0]
        allowed_hosts = ("bilibili.com", "hdslb.com")

        def allowed(url: str) -> bool:
            target = urlparse(url)
            hostname = (target.hostname or "").lower().rstrip(".")
            host_allowed = any(hostname == host or hostname.endswith("." + host) for host in allowed_hosts)
            return target.scheme in {"http", "https"} and host_allowed

        if not allowed(remote_url):
            _send_json(self, {"error": "无效的封面地址。"}, HTTPStatus.BAD_REQUEST)
            return
        try:
            import httpx

            current_url = remote_url
            response = None
            body = None
            for _ in range(4):
                if not allowed(current_url):
                    raise ValueError("封面重定向到了不受信任的地址。")
                with httpx.Client(
                    timeout=15,
                    follow_redirects=False,
                    headers={"Referer": "https://www.bilibili.com/", "User-Agent": "Mozilla/5.0 bili-agent/0.1"},
                ) as client:
                    with client.stream("GET", current_url) as response:
                        if 300 <= response.status_code < 400:
                            location = response.headers.get("location")
                            if not location:
                                raise ValueError("封面重定向缺少目标地址。")
                            current_url = urljoin(current_url, location)
                            continue
                        if not allowed(str(response.url)):
                            raise ValueError("封面最终地址不受信任。")
                        response.raise_for_status()
                        content_type = response.headers.get("content-type", "image/jpeg").split(";", 1)[0]
                        if not content_type.startswith("image/"):
                            raise ValueError("远程资源不是图片。")
                        content_length = response.headers.get("content-length")
                        if content_length and int(content_length) > self.settings.max_cover_bytes:
                            raise ValueError("封面文件过大。")
                        chunks: list[bytes] = []
                        total = 0
                        for chunk in response.iter_bytes():
                            total += len(chunk)
                            if total > self.settings.max_cover_bytes:
                                raise ValueError("封面文件过大。")
                            chunks.append(chunk)
                        body = b"".join(chunks)
                        break
            else:
                raise ValueError("封面重定向次数过多。")

            if response is None or body is None:
                raise ValueError("封面下载失败。")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "public, max-age=3600")
            self.end_headers()
            self.wfile.write(body)
        except Exception as exc:
            LOGGER.warning("封面代理失败: %s", exc)
            _send_json(self, {"error": "封面暂时无法加载。"}, HTTPStatus.BAD_GATEWAY)

    def _serve_audio(self, parsed) -> None:
        """Serve only audio belonging to an analyzed session/page, with ranges."""
        query = parse_qs(parsed.query)
        session_id = query.get("session_id", [""])[0]
        raw_page_index = query.get("page_index", [""])[0]
        if not session_id or not raw_page_index.isdigit():
            _send_json(self, {"error": "音频参数无效。"}, HTTPStatus.BAD_REQUEST)
            return
        result = self.store.get(session_id)
        if not result:
            _send_json(self, {"error": "分析会话不存在或已过期。"}, HTTPStatus.NOT_FOUND)
            return
        page_index = int(raw_page_index)
        page_analysis = next((item for item in result.pages if item.page.page_index == page_index), None)
        if page_analysis is None:
            _send_json(self, {"error": "该分P不在当前分析会话中。"}, HTTPStatus.NOT_FOUND)
            return
        page = page_analysis.page
        page_url = f"{result.metadata.url}{'&' if '?' in result.metadata.url else '?'}p={page.page_index + 1}"
        try:
            downloader = AudioDownloader(self.settings.asr_cache_dir, self.settings.asr_audio_format)
            audio_path = asyncio.run(downloader.download(page_url, page))
            audio_path = Path(audio_path).resolve()
            cache_root = self.settings.asr_cache_dir.resolve()
            if cache_root not in audio_path.parents or not audio_path.is_file():
                raise ValueError("音频文件路径不在缓存目录内。")
            self._send_audio_file(audio_path)
        except AudioDownloadError as exc:
            LOGGER.warning("音频对照加载失败 session=%s p=%s code=%s: %s", session_id[:8], page_index + 1, exc.code, exc)
            _send_json(self, {"error": str(exc), "code": exc.code}, HTTPStatus.BAD_GATEWAY)
        except Exception as exc:
            LOGGER.warning("音频对照加载失败 session=%s p=%s: %s", session_id[:8], page_index + 1, exc)
            _send_json(self, {"error": "音频暂时无法加载，请确认 yt-dlp 和 ffmpeg 可用。"}, HTTPStatus.BAD_GATEWAY)

    def _send_audio_file(self, path: Path) -> None:
        size = path.stat().st_size
        start = 0
        end = size - 1
        range_header = self.headers.get("Range", "")
        if range_header.startswith("bytes="):
            requested = range_header.removeprefix("bytes=").split(",", 1)[0].strip()
            left, _, right = requested.partition("-")
            if left.isdigit():
                start = int(left)
                end = int(right) if right.isdigit() else end
            elif right.isdigit():
                length = int(right)
                start = max(size - length, 0)
            if start >= size or start > end:
                self.send_response(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
                self.send_header("Content-Range", f"bytes */{size}")
                self.end_headers()
                return
            end = min(end, size - 1)
        length = end - start + 1
        content_type = mimetypes.guess_type(str(path))[0] or "audio/mp4"
        self.send_response(HTTPStatus.PARTIAL_CONTENT if range_header else HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(length))
        self.send_header("Accept-Ranges", "bytes")
        if range_header:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.send_header("Cache-Control", "private, max-age=3600")
        self.end_headers()
        with path.open("rb") as stream:
            stream.seek(start)
            remaining = length
            while remaining:
                chunk = stream.read(min(1024 * 1024, remaining))
                if not chunk:
                    break
                self.wfile.write(chunk)
                remaining -= len(chunk)

    def _serve_static(self, path: str) -> None:
        relative = unquote(path.removeprefix("/")) or "index.html"
        requested = (STATIC_DIR / relative).resolve()
        if STATIC_DIR not in requested.parents and requested != STATIC_DIR:
            _send_json(self, {"error": "无效路径。"}, HTTPStatus.NOT_FOUND)
            return
        if not requested.is_file():
            requested = STATIC_DIR / "index.html"
        content = requested.read_bytes()
        content_type = mimetypes.guess_type(str(requested))[0] or "application/octet-stream"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", f"{content_type}; charset=utf-8" if content_type.startswith("text/") else content_type)
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)


def serve(settings: Settings, host: str = "127.0.0.1", port: int = 8765, open_browser: bool = False) -> None:
    WebHandler.settings = settings
    WebHandler.storage = SQLiteStore(settings.storage_db_path)
    WebHandler.store = SessionStore(
        ttl_seconds=settings.session_ttl_seconds,
        max_items=settings.max_sessions,
        max_history_turns=settings.max_history_turns,
        storage=WebHandler.storage,
    )
    WebHandler.jobs = JobStore(
        ttl_seconds=settings.job_ttl_seconds,
        max_items=settings.max_jobs,
        storage=WebHandler.storage,
    )
    WebHandler.analysis_slots = threading.BoundedSemaphore(settings.max_concurrent_analyses)
    server = ThreadingHTTPServer((host, port), WebHandler)
    url = f"http://{host}:{port}"
    LOGGER.info("聊天面板已启动: %s", url)
    if open_browser:
        threading.Timer(0.4, webbrowser.open, args=(url,)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        LOGGER.info("正在关闭聊天面板。")
    finally:
        server.server_close()
        if WebHandler.storage:
            WebHandler.storage.close()
            WebHandler.storage = None
