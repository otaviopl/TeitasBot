import socket

import pytest
import requests


def test_socket_connections_are_blocked():
    with pytest.raises(AssertionError, match="External network/API call detected"):
        socket.create_connection(("example.com", 80), timeout=0.1)


def test_requests_calls_are_blocked():
    with pytest.raises(AssertionError, match="External network/API call detected"):
        requests.get("https://example.com", timeout=0.1)


def test_httpx_calls_are_blocked():
    httpx = pytest.importorskip("httpx")
    with pytest.raises(AssertionError, match="External network/API call detected"):
        httpx.get("https://example.com", timeout=0.1)
