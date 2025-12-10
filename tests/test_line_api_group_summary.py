from types import SimpleNamespace
from unittest.mock import MagicMock

from src.infra.line_api import LineApiAdapter


class _FakeResponse:
    def __init__(self, status_code=200, json_data=None, text=""):
        self.status_code = status_code
        self._json = json_data or {}
        self.text = text

    @property
    def ok(self):
        return 200 <= self.status_code < 300

    def json(self):
        return self._json


class _FakeSession:
    def __init__(self, responses):
        self._responses = responses
        self.headers = {}
        self.called_urls = []

    def get(self, url, timeout=5):
        self.called_urls.append((url, timeout))
        return self._responses.pop(0)

    def post(self, *args, **kwargs):
        raise AssertionError("post should not be called in these tests")


def _adapter_with_responses(responses):
    fake_session = _FakeSession(responses)
    adapter = LineApiAdapter("dummy")
    adapter._session = fake_session  # type: ignore[attr-defined]
    return adapter, fake_session


def test_get_group_name_success():
    adapter, session = _adapter_with_responses([_FakeResponse(json_data={"groupName": "G"})])

    name = adapter.get_group_name("group123")

    assert name == "G"
    assert session.called_urls[0][0].endswith("/group/group123/summary")


def test_get_group_name_404_returns_none():
    adapter, _ = _adapter_with_responses([_FakeResponse(status_code=404)])

    name = adapter.get_group_name("group123")

    assert name is None


def test_get_group_name_error_returns_none():
    adapter, _ = _adapter_with_responses([_FakeResponse(status_code=500, text="error")])

    name = adapter.get_group_name("group123")

    assert name is None
