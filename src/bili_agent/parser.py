from __future__ import annotations

import re
from urllib.parse import unquote, urlparse

from .models import VideoIdentifier


class InputParseError(ValueError):
    """Raised when an input is not a supported Bilibili video identifier."""


_BVID = re.compile(r"\b(BV[0-9A-Za-z]{10})\b", re.IGNORECASE)
_AID = re.compile(r"\b(?:av|AV)(\d+)\b")
_BARE_BVID = re.compile(r"^BV[0-9A-Za-z]{10}$", re.IGNORECASE)
_BARE_AID = re.compile(r"^(?:av|AV)\d+$")


def _video_identifier_from_text(raw: str) -> VideoIdentifier | None:
    bvid_match = _BVID.search(raw)
    if bvid_match:
        bvid = bvid_match.group(1)
        if bvid[:2].lower() == "bv":
            bvid = "BV" + bvid[2:]
        return VideoIdentifier(kind="bvid", value=bvid, canonical_url=f"https://www.bilibili.com/video/{bvid}")

    aid_match = _AID.search(raw)
    if aid_match:
        aid = aid_match.group(1)
        return VideoIdentifier(kind="aid", value=aid, canonical_url=f"https://www.bilibili.com/video/av{aid}")
    return None


def parse_video_input(value: str) -> VideoIdentifier:
    raw = unquote(value.strip())
    if not raw:
        raise InputParseError("视频输入不能为空。")

    if _BARE_BVID.fullmatch(raw) or _BARE_AID.fullmatch(raw):
        identifier = _video_identifier_from_text(raw)
        if identifier:
            return identifier

    parsed = urlparse(raw if "://" in raw else f"https://{raw}")
    hostname = (parsed.hostname or "").lower().rstrip(".")
    allowed = hostname == "bilibili.com" or hostname.endswith(".bilibili.com") or hostname == "b23.tv"
    if allowed:
        identifier = _video_identifier_from_text(raw)
        if identifier:
            return identifier
        raise InputParseError("未在 Bilibili 视频链接中找到有效的 BV 号或 av 号。")
    raise InputParseError("请输入 Bilibili 视频链接、BV 号或 av 号。")
