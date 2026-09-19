import pytest

from bili_agent.parser import InputParseError, parse_video_input


def test_parse_bv_url():
    identifier = parse_video_input("https://www.bilibili.com/video/BV1xx411c7mD?p=2")
    assert identifier.kind == "bvid"
    assert identifier.value == "BV1xx411c7mD"


def test_parse_av():
    identifier = parse_video_input("av170001")
    assert identifier.kind == "aid"
    assert identifier.value == "170001"


def test_reject_unknown_input():
    with pytest.raises(InputParseError):
        parse_video_input("https://example.com/video/123")


def test_reject_bvid_embedded_in_foreign_url():
    with pytest.raises(InputParseError):
        parse_video_input("https://example.com/video/BV1xx411c7mD")
