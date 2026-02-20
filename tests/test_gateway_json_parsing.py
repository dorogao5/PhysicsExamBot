import json

from src.llm.gateway import DashScopeChatClient


def test_parse_json_response_text_plain() -> None:
    value = DashScopeChatClient._parse_json_response_text('{"topics": []}')
    assert value == {"topics": []}


def test_parse_json_response_text_markdown_fence() -> None:
    value = DashScopeChatClient._parse_json_response_text('```json\n{"topics": []}\n```')
    assert value == {"topics": []}


def test_parse_json_response_text_with_prefix_suffix() -> None:
    value = DashScopeChatClient._parse_json_response_text('answer:\n{"topics": []}\nthanks')
    assert value == {"topics": []}


def test_parse_json_response_text_raises_on_truncated() -> None:
    try:
        DashScopeChatClient._parse_json_response_text('{"topics": ["x"')
    except json.JSONDecodeError:
        pass
    else:
        raise AssertionError("Expected JSONDecodeError")


def test_looks_truncated_json() -> None:
    assert DashScopeChatClient._looks_truncated_json('{"a": 1') is True
    assert DashScopeChatClient._looks_truncated_json('{"a": 1}') is False
