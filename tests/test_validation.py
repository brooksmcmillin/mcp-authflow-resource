"""Tests for mcp_authflow_resource.validation module."""

import json
from typing import Any

from mcp_authflow_resource.validation import (
    json_error,
    require_dict,
    require_list,
    validate_dict_response,
    validate_list_response,
)


class MockResponse:
    """Minimal mock that satisfies the ApiResponse Protocol."""

    def __init__(self, success: bool, data: Any = None, error: str | None = None) -> None:  # noqa: ANN401
        self.success = success
        self.data = data
        self.error = error


# ---------------------------------------------------------------------------
# json_error
# ---------------------------------------------------------------------------


class TestJsonError:
    def test_returns_json_string(self) -> None:
        result = json_error("something went wrong")
        assert isinstance(result, str)
        parsed = json.loads(result)
        assert parsed == {"error": "something went wrong"}

    def test_empty_message(self) -> None:
        result = json_error("")
        parsed = json.loads(result)
        assert parsed == {"error": ""}

    def test_message_preserved_verbatim(self) -> None:
        msg = 'line1\nline2 "quoted"'
        result = json_error(msg)
        parsed = json.loads(result)
        assert parsed["error"] == msg


# ---------------------------------------------------------------------------
# validate_list_response
# ---------------------------------------------------------------------------


class TestValidateListResponse:
    def test_valid_list_returned(self) -> None:
        items = [{"id": 1, "name": "a"}, {"id": 2, "name": "b"}]
        resp = MockResponse(success=True, data=items)
        result, error = validate_list_response(resp, "items")
        assert error is None
        assert result == items

    def test_failed_response_uses_error_field(self) -> None:
        resp = MockResponse(success=False, error="unauthorized")
        result, error = validate_list_response(resp, "items")
        assert result == []
        assert error == "unauthorized"

    def test_failed_response_no_error_field_uses_context(self) -> None:
        resp = MockResponse(success=False, error=None)
        result, error = validate_list_response(resp, "widgets")
        assert result == []
        assert error == "Failed to fetch widgets"

    def test_none_data_returns_empty_no_error(self) -> None:
        resp = MockResponse(success=True, data=None)
        result, error = validate_list_response(resp, "items")
        assert result == []
        assert error is None

    def test_non_list_non_dict_data_returns_error(self) -> None:
        resp = MockResponse(success=True, data=42)
        result, error = validate_list_response(resp, "items")
        assert result == []
        assert error is not None
        assert "expected list" in error
        assert "int" in error

    def test_wrapped_dict_with_explicit_key(self) -> None:
        inner = [{"id": 1}]
        resp = MockResponse(success=True, data={"tasks": inner})
        result, error = validate_list_response(resp, "items", key="tasks")
        assert error is None
        assert result == inner

    def test_wrapped_dict_with_context_as_key(self) -> None:
        inner = [{"id": 1}]
        resp = MockResponse(success=True, data={"tasks": inner})
        result, error = validate_list_response(resp, "tasks")
        assert error is None
        assert result == inner

    def test_wrapped_dict_with_pluralised_context_key(self) -> None:
        inner = [{"id": 1}]
        resp = MockResponse(success=True, data={"tasks": inner})
        # context is "task", plural "tasks" should match
        result, error = validate_list_response(resp, "task")
        assert error is None
        assert result == inner

    def test_wrapped_dict_no_matching_key_returns_error(self) -> None:
        resp = MockResponse(success=True, data={"other": [1, 2]})
        result, error = validate_list_response(resp, "items")
        assert result == []
        assert error is not None
        assert "items" in error
        assert "unexpected format" in error

    def test_wrapped_dict_no_matching_key_does_not_leak_keys(self) -> None:
        # The client-facing error must not expose backend field names (CWE-209).
        resp = MockResponse(success=True, data={"secret_field": 1, "internal_id": 2})
        result, error = validate_list_response(resp, "items")
        assert result == []
        assert error is not None
        assert "secret_field" not in error
        assert "internal_id" not in error

    def test_non_dict_items_in_list_are_skipped(self) -> None:
        items: list[Any] = [{"id": 1}, "bad", {"id": 2}, None]
        resp = MockResponse(success=True, data=items)
        result, error = validate_list_response(resp, "items")
        assert error is None
        assert result == [{"id": 1}, {"id": 2}]

    def test_all_invalid_items_returns_empty_list(self) -> None:
        resp = MockResponse(success=True, data=["a", "b", 3])
        result, error = validate_list_response(resp, "items")
        assert error is None
        assert result == []


# ---------------------------------------------------------------------------
# validate_dict_response
# ---------------------------------------------------------------------------


class TestValidateDictResponse:
    def test_valid_dict_returned(self) -> None:
        data = {"id": 1, "title": "task"}
        resp = MockResponse(success=True, data=data)
        result, error = validate_dict_response(resp, "task")
        assert error is None
        assert result == data

    def test_failed_response_uses_error_field(self) -> None:
        resp = MockResponse(success=False, error="not found")
        result, error = validate_dict_response(resp, "task")
        assert result is None
        assert error == "not found"

    def test_failed_response_no_error_field_uses_context(self) -> None:
        resp = MockResponse(success=False, error=None)
        result, error = validate_dict_response(resp, "project")
        assert result is None
        assert error == "Failed to fetch project"

    def test_none_data_returns_error(self) -> None:
        resp = MockResponse(success=True, data=None)
        result, error = validate_dict_response(resp, "task")
        assert result is None
        assert error == "No task data returned from backend"

    def test_non_dict_data_returns_error(self) -> None:
        resp = MockResponse(success=True, data=[1, 2, 3])
        result, error = validate_dict_response(resp, "task")
        assert result is None
        assert error is not None
        assert "expected dict" in error
        assert "list" in error

    def test_non_dict_string_data_returns_error(self) -> None:
        resp = MockResponse(success=True, data="raw string")
        result, error = validate_dict_response(resp, "task")
        assert result is None
        assert error is not None
        assert "str" in error


# ---------------------------------------------------------------------------
# require_list
# ---------------------------------------------------------------------------


class TestRequireList:
    def test_returns_list_on_success(self) -> None:
        items = [{"id": 1}]
        resp = MockResponse(success=True, data=items)
        result = require_list(resp, "items")
        assert result == items

    def test_returns_json_error_string_on_failure(self) -> None:
        resp = MockResponse(success=False, error="boom")
        result = require_list(resp, "items")
        assert isinstance(result, str)
        parsed = json.loads(result)
        assert "error" in parsed
        assert "boom" in parsed["error"]

    def test_returns_json_error_on_bad_type(self) -> None:
        resp = MockResponse(success=True, data=99)
        result = require_list(resp, "items")
        assert isinstance(result, str)
        parsed = json.loads(result)
        assert "error" in parsed

    def test_passes_key_through(self) -> None:
        inner = [{"id": 5}]
        resp = MockResponse(success=True, data={"results": inner})
        result = require_list(resp, "items", key="results")
        assert result == inner


# ---------------------------------------------------------------------------
# require_dict
# ---------------------------------------------------------------------------


class TestRequireDict:
    def test_returns_dict_on_success(self) -> None:
        data = {"id": 7, "name": "x"}
        resp = MockResponse(success=True, data=data)
        result = require_dict(resp, "item")
        assert result == data

    def test_returns_json_error_string_on_failure(self) -> None:
        resp = MockResponse(success=False, error="server error")
        result = require_dict(resp, "item")
        assert isinstance(result, str)
        parsed = json.loads(result)
        assert "error" in parsed
        assert "server error" in parsed["error"]

    def test_returns_json_error_on_none_data(self) -> None:
        resp = MockResponse(success=True, data=None)
        result = require_dict(resp, "item")
        assert isinstance(result, str)
        parsed = json.loads(result)
        assert "error" in parsed

    def test_returns_json_error_on_non_dict_data(self) -> None:
        resp = MockResponse(success=True, data=[1, 2])
        result = require_dict(resp, "item")
        assert isinstance(result, str)
        parsed = json.loads(result)
        assert "error" in parsed

    def test_no_error_field_falls_back_to_context(self) -> None:
        resp = MockResponse(success=False, error=None)
        result = require_dict(resp, "widget")
        assert isinstance(result, str)
        parsed = json.loads(result)
        assert "widget" in parsed["error"]
