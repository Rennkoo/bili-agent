from __future__ import annotations

import json

from bili_agent.cli import _parser, _print_diagnostics


def test_doctor_parser_accepts_json_flag():
    args = _parser().parse_args(["doctor", "--json"])
    assert args.command == "doctor"
    assert args.json is True


def test_print_diagnostics_json_contains_only_safe_fields(capsys):
    _print_diagnostics(
        {
            "version": "0.1.0",
            "python": "3.11.0",
            "llm_configured": True,
            "auth_required": True,
            "capabilities": {"asr": False, "yt_dlp": True, "ffmpeg": False, "vision": False},
            "storage": {"writable": True},
            "warnings": [],
        },
        as_json=True,
    )
    output = capsys.readouterr().out
    parsed = json.loads(output)
    assert parsed["version"] == "0.1.0"
    assert "llm_api_key" not in output
    assert parsed["auth_required"] is True
