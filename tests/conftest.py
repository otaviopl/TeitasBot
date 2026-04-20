import sys
import socket

import bcrypt
import pytest

_original_gensalt = bcrypt.gensalt


def _fast_gensalt(rounds=4, prefix=b"2b"):
    """Use minimal bcrypt rounds in tests (~0.002s vs ~0.6s per hash)."""
    return _original_gensalt(rounds=4, prefix=prefix)


BLOCK_MESSAGE = (
    "External network/API call detected during tests. "
    "Mock the integration explicitly."
)


def _blocked_external_call(*_args, **_kwargs):
    raise AssertionError(BLOCK_MESSAGE)


@pytest.fixture(autouse=True)
def block_external_calls(monkeypatch):
    monkeypatch.setattr(bcrypt, "gensalt", _fast_gensalt)
    monkeypatch.setattr(socket, "create_connection", _blocked_external_call, raising=True)
    monkeypatch.setattr(socket.socket, "connect", _blocked_external_call, raising=True)
    monkeypatch.setattr(socket.socket, "connect_ex", _blocked_external_call, raising=True)

    try:
        import requests.sessions as requests_sessions

        monkeypatch.setattr(requests_sessions.Session, "request", _blocked_external_call, raising=True)
    except Exception:
        pass

    try:
        import httpx

        monkeypatch.setattr(httpx.Client, "request", _blocked_external_call, raising=True)
        monkeypatch.setattr(httpx.AsyncClient, "request", _blocked_external_call, raising=True)
    except Exception:
        pass

    try:
        import googleapiclient.discovery as google_discovery

        monkeypatch.setattr(google_discovery, "build", _blocked_external_call, raising=True)
    except Exception:
        pass

    openai_module = sys.modules.get("openai")
    if openai_module is not None:
        monkeypatch.setattr(openai_module, "OpenAI", _blocked_external_call, raising=False)
