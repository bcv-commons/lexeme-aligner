"""hf_bulk_publish: HF client timeout + retry of HF server errors (2026-10-21: a 502 stopped a push, and a commit whose reply never came
back left the push waiting 22 min on a dead connection)."""
import httpx
import pytest
from huggingface_hub.errors import HfHubHTTPError

from lexeme_aligner import hf_bulk_publish as hb


def _http_error(status: int) -> HfHubHTTPError:
    req = httpx.Request("POST", "https://huggingface.co/api/datasets/x/commit/main")
    return HfHubHTTPError(f"{status}", response=httpx.Response(status, request=req))


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr(hb.time, "sleep", lambda s: None)


def test_server_error_is_retried():
    calls = []

    def fn():
        calls.append(1)
        if len(calls) < 3:
            raise _http_error(502)
        return "ok"
    assert hb._retry_transient(fn, "commit") == "ok" and len(calls) == 3


@pytest.mark.parametrize("status", [401, 403, 429])
def test_client_errors_propagate_at_once(status):
    calls = []

    def fn():
        calls.append(1)
        raise _http_error(status)
    with pytest.raises(HfHubHTTPError):
        hb._retry_transient(fn, "commit")
    assert len(calls) == 1


def test_timeout_is_retried_and_last_error_raised():
    calls = []

    def fn():
        calls.append(1)
        raise httpx.ReadTimeout("no reply")
    with pytest.raises(httpx.ReadTimeout):
        hb._retry_transient(fn, "commit", attempts=3)
    assert len(calls) == 3


def test_install_timeout_gives_the_hf_client_a_timeout():
    from huggingface_hub.utils._http import close_session, get_session, hf_request_event_hook
    hb.install_timeout(42.0)
    close_session()
    try:
        c = get_session()
        assert c.timeout.read == 42.0 and c.timeout.connect == 30.0
        assert hf_request_event_hook in c.event_hooks["request"]
    finally:
        hb.install_timeout()
        close_session()
