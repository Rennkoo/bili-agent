from __future__ import annotations

import asyncio
import hmac
import json
import logging
import mimetypes
import threading
import uuid
import webbrowser
from dataclasses import replace
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urljoin, urlparse

from .agent import BiliAgent
from .config import Settings
from .infographic import render_infographic
from .markdown import render_markdown
from .models import AnalysisResult
from .parser import InputParseError, parse_video_input
from .skills import skill_label

LOGGER = logging.getLogger(__name__)
STATIC_DIR = Path(__file__).with_name("static")
ENV_PATH = Path.cwd() / ".env"
SETTINGS_LOCK = threading.Lock()


class SessionStore:
    def __init__(self):
        self._items: dict[str, AnalysisResult] = {}
        self._history: dict[str, list[dict[str, str]]] = {}
        self._lock = threading.Lock()

    def put(self, result: AnalysisResult) -> str:
        session_id = uuid.uuid4().hex
        with self._lock:
            self._items[session_id] = result
            self._history[session_id] = []
        return session_id

    def get(self, session_id: str) -> AnalysisResult | None:
        with self._lock:
            return self._items.get(session_id)

    def history(self, session_id: str) -> list[dict[str, str]]:
        with self._lock:
            return list(self._history.get(session_id, []))

    def add_turn(self, session_id: str, role: str, content: str) -> None:
        with self._lock:
            if session_id in self._history:
                self._history[session_id].append({"role": role, "content": content})


class JobStore:
    def __init__(self):
        self._items: dict[str, dict] = {}
        self._lock = threading.Lock()

    def create(self) -> str:
        job_id = uuid.uuid4().hex
        with self._lock:
            self._items[job_id] = {
                "job_id": job_id,
                "status": "queued",
                "stage": "queued",
                "progress": 0,
                "message": "等待开始",
            }
        return job_id

    def update(self, job_id: str, **values) -> None:
        with self._lock:
            if job_id in self._items:
                self._items[job_id].update(values)

    def get(self, job_id: str) -> dict | None:
        with self._lock:
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
            _send_json(
                self,
                {
                    "status": "ok",
                    "llm_configured": bool(self.settings.llm_api_key),
                    "auth_required": bool(self.settings.web_auth_token),
                },
            )
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
            args=(job_id, video, enable_asr, enable_multimodal),
            daemon=True,
            name=f"bili-agent-{job_id[:8]}",
        )
        try:
            thread.start()
        except Exception:
            self.analysis_slots.release()
            raise
        _send_json(self, {"job_id": job_id}, HTTPStatus.ACCEPTED)

    def _save_settings(self, payload: dict) -> None:
        base_url = str(payload.get("llm_base_url", "")).strip()
        model = str(payload.get("llm_model", "")).strip()
        api_key = payload.get("llm_api_key")
        clear_key = bool(payload.get("clear_api_key", False))
        if not base_url:
            base_url = "https://api.openai.com/v1"
        if not model:
            raise ValueError("模型名不能为空。")
        if api_key is not None and not isinstance(api_key, str):
            raise ValueError("API Key 必须是文本。")
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

    def _run_analysis_job(self, job_id: str, video: str, enable_asr: bool, enable_multimodal: bool) -> None:
        async def progress(stage: str, percent: int, message: str) -> None:
            self.jobs.update(job_id, status="running", stage=stage, progress=percent, message=message)

        try:
            result = _run(
                BiliAgent(self.settings).analyze(
                    video,
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
