"""Outbound webhooks for organisation alerts (PRD F6).

The URL is supplied by organisation admins and called from our servers, so it is a
server-side request forgery risk. Every resolved address must be public (no private,
loopback, link-local or metadata addresses), HTTPS is required, and redirects are never
followed. Checks run when the URL is saved and again before each send, since DNS can change.
"""

import hashlib
import hmac
import ipaddress
import json
import secrets
import socket
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import httpx

from app.config import APP_NAME

TIMEOUT_SECONDS = 10
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


class UnsafeWebhook(ValueError):
    pass


def new_secret() -> str:
    return "whsec_" + secrets.token_urlsafe(32)


def _resolve(host: str, port: int) -> list[str]:
    return [info[4][0] for info in socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)]


def validate_webhook_url(url: str, allow_local: bool = False) -> str:
    """Return the URL if it is safe to call; raise UnsafeWebhook otherwise.

    `allow_local` permits http://localhost for development only.
    """
    try:
        parts = urlsplit(url)
        port = parts.port or (443 if parts.scheme == "https" else 80)
    except ValueError as exc:
        raise UnsafeWebhook("Not a valid URL") from exc
    host = (parts.hostname or "").lower()
    if not host or parts.username or parts.password:
        raise UnsafeWebhook("URL needs a host and must not contain credentials")
    local = allow_local and host in LOCAL_HOSTS
    if parts.scheme != "https" and not (local and parts.scheme == "http"):
        raise UnsafeWebhook("Webhook URLs must use https")
    try:
        addresses = _resolve(host, port)
    except OSError as exc:
        raise UnsafeWebhook(f"Can't resolve {host}") from exc
    if not addresses:
        raise UnsafeWebhook(f"Can't resolve {host}")
    for address in addresses:
        ip = ipaddress.ip_address(address.split("%")[0])
        if not (ip.is_global or (local and ip.is_loopback)):
            raise UnsafeWebhook("Webhook URLs must point to a public internet address")
    return url


def sign(secret: str, timestamp: str, body: bytes) -> str:
    """HMAC-SHA256 over "<timestamp>.<body>" (receivers should reject old timestamps)."""
    digest = hmac.new(secret.encode(), timestamp.encode() + b"." + body, hashlib.sha256)
    return "sha256=" + digest.hexdigest()


@dataclass(frozen=True)
class DeliveryResult:
    ok: bool
    http_status: int | None = None
    error: str | None = None


def send(
    url: str,
    secret: str,
    payload: dict[str, Any],
    allow_local: bool = False,
    transport: httpx.BaseTransport | None = None,
) -> DeliveryResult:
    try:
        validate_webhook_url(url, allow_local)
    except UnsafeWebhook as exc:
        return DeliveryResult(False, error=str(exc))
    body = json.dumps(payload, default=str, separators=(",", ":")).encode()
    timestamp = str(int(time.time()))
    headers = {
        "Content-Type": "application/json",
        "User-Agent": f"{APP_NAME}-Webhooks/1.0",
        f"X-{APP_NAME}-Timestamp": timestamp,
        f"X-{APP_NAME}-Signature": sign(secret, timestamp, body),
    }
    try:
        with httpx.Client(
            timeout=TIMEOUT_SECONDS, follow_redirects=False, transport=transport
        ) as client:
            response = client.post(url, content=body, headers=headers)
    except httpx.HTTPError as exc:
        return DeliveryResult(False, error=f"{type(exc).__name__}: {exc}"[:300])
    return DeliveryResult(200 <= response.status_code < 300, response.status_code)
