import hashlib
import hmac
import json

import httpx
import pytest

from app.services import webhooks
from app.services.webhooks import UnsafeWebhook, sign, validate_webhook_url

PUBLIC = "93.184.215.14"


@pytest.fixture
def dns(monkeypatch):
    table = {"hooks.example.org": [PUBLIC], "localhost": ["127.0.0.1"]}

    def resolve(host, port):
        if host not in table:
            raise OSError("no such host")
        return table[host]

    monkeypatch.setattr(webhooks, "_resolve", resolve)
    return table


def test_public_https_urls_are_allowed(dns):
    assert validate_webhook_url("https://hooks.example.org/dh") == "https://hooks.example.org/dh"


@pytest.mark.parametrize(
    ("url", "address"),
    [
        ("https://internal.example.org/x", "10.0.0.5"),
        ("https://metadata.example.org/x", "169.254.169.254"),  # cloud metadata server
        ("https://loop.example.org/x", "127.0.0.1"),
        ("https://v6.example.org/x", "::1"),
        ("https://lan.example.org/x", "192.168.1.69"),
    ],
)
def test_private_and_metadata_addresses_are_refused(dns, url, address):
    dns[url.split("/")[2]] = [address]
    with pytest.raises(UnsafeWebhook):
        validate_webhook_url(url)


@pytest.mark.parametrize(
    "url",
    [
        "http://hooks.example.org/dh",  # not https
        "https://user:pw@hooks.example.org/dh",  # credentials
        "https://nowhere.invalid/dh",  # unresolvable
        "not a url",
    ],
)
def test_other_unsafe_urls(dns, url):
    with pytest.raises(UnsafeWebhook):
        validate_webhook_url(url)


def test_plain_http_localhost_only_in_development(dns):
    with pytest.raises(UnsafeWebhook):
        validate_webhook_url("http://localhost:9000/hook")
    assert validate_webhook_url("http://localhost:9000/hook", allow_local=True)


def test_send_signs_the_body_and_never_follows_redirects(dns):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["request"] = request
        return httpx.Response(302, headers={"Location": "http://169.254.169.254/"})

    result = webhooks.send(
        "https://hooks.example.org/dh",
        "whsec_test",
        {"a": 1},
        transport=httpx.MockTransport(handler),
    )
    assert not result.ok and result.http_status == 302  # the redirect wasn't followed
    request = seen["request"]
    timestamp = request.headers["x-vanya-timestamp"]
    expected = hmac.new(b"whsec_test", timestamp.encode() + b"." + request.content, hashlib.sha256)
    assert request.headers["x-vanya-signature"] == "sha256=" + expected.hexdigest()
    assert json.loads(request.content) == {"a": 1}


def test_send_refuses_unsafe_urls_without_calling_them(dns):
    dns["evil.example.org"] = ["10.1.2.3"]
    result = webhooks.send(
        "https://evil.example.org/x",
        "s",
        {},
        transport=httpx.MockTransport(lambda r: pytest.fail("must not be called")),
    )
    assert not result.ok and "public" in result.error


def test_signature_is_stable():
    assert sign("k", "1", b"{}") == sign("k", "1", b"{}") != sign("k", "2", b"{}")
