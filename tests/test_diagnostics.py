from pathlib import Path

from bili_agent.config import Settings
from bili_agent.diagnostics import runtime_diagnostics


def _settings(tmp_path: Path) -> Settings:
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
        asr_cache_dir=tmp_path / "audio",
        storage_db_path=tmp_path / "state" / "agent.sqlite3",
    )


def test_runtime_diagnostics_is_safe_and_reports_fallback(tmp_path):
    result = runtime_diagnostics(_settings(tmp_path))

    assert result["status"] == "ok"
    assert result["ready"] is True
    assert result["llm_configured"] is False
    assert result["storage"]["writable"] is True
    assert any("LLM_API_KEY" in warning for warning in result["warnings"])
    assert "super-secret" not in str(result)
