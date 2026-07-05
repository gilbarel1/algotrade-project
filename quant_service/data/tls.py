"""Shared TLS trust for outbound httpx calls (news fetch, §3.1).

The Step-1 Yahoo path solves corporate TLS interception for its curl_cffi
backend (`yahoo.configure_tls`). The news fetch uses `httpx`, which verifies via
OpenSSL and so needs its own trust anchor: on a machine behind a TLS-inspecting
proxy the interceptor's root CA lives in the OS store, not in certifi's bundle,
and some such roots trip OpenSSL's stricter parsing outright.

`truststore` verifies against the OS trust store natively (Windows CryptoAPI /
macOS Security / Linux OpenSSL dirs), which both trusts the corporate root and
tolerates its quirks — with verification fully **on** (never `verify=False`,
§ security guardrail). When `truststore` is unavailable we fall back to certifi's
default context, which is correct on a clean (non-intercepted) network.
"""

from __future__ import annotations

import ssl
from functools import lru_cache


@lru_cache(maxsize=1)
def ssl_context() -> ssl.SSLContext:
    """Return a verifying SSLContext suitable for httpx ``verify=``.

    Prefers the OS trust store via ``truststore``; falls back to the stdlib
    default context (certifi-backed) if it is not installed.
    """
    try:
        import truststore

        return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    except Exception:  # noqa: BLE001 - any failure -> safe verifying default
        return ssl.create_default_context()
