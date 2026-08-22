"""HTTP layer.

Holds the single slowapi limiter so `main.py` and the endpoint modules agree on one
instance. It lives here rather than in ``app/api/router.py`` because the endpoint modules
import it, and ``router.py`` imports the endpoint modules — the package ``__init__`` is the
only spot in this import graph that both sides can reach without a cycle.
"""

from __future__ import annotations

import ipaddress

from slowapi import Limiter
from slowapi.util import get_remote_address
from starlette.requests import Request


def _peer_is_a_proxy(request: Request) -> bool:
    """Whether the socket this request arrived on belongs to infrastructure, not the internet.

    ``X-Forwarded-For`` is a header, so a caller can put anything in it. It may only be
    believed when the machine that actually opened the connection is a proxy in front of us —
    a private or loopback address. When the peer is a public address, the request reached
    this process directly and every hop it claims is fiction.

    Fails closed on anything unparseable, which includes the literal peer name a test client
    reports: an unrecognised peer is not a proxy.

    Args:
        request: The incoming request.

    Returns:
        True when the immediate peer is a private, loopback or link-local address.
    """
    try:
        address = ipaddress.ip_address(get_remote_address(request))
    except ValueError:
        return False
    return address.is_private or address.is_loopback or address.is_link_local


def rate_limit_key(request: Request) -> str:
    """Identify the caller for rate limiting by network origin.

    Never a client-supplied identifier: anything the caller mints, it can rotate, and a
    budget that resets on demand is not a budget. This is the right key for the routes that
    have no caller yet — sign-in, registration, the unauthenticated 401s — and those are
    exactly the routes where getting it wrong means unlimited password guessing.

    ``X-Forwarded-For`` is therefore read **only** behind a proxy. It used to be read
    unconditionally, which meant one header made every unauthenticated limit in the app
    unenforceable: rotating it gave unbounded login attempts, unbounded username enumeration
    through the duplicate-name error, and unbounded pending accounts in the approval queue.

    Args:
        request: The incoming request.

    Returns:
        The client's IP address.
    """
    if _peer_is_a_proxy(request):
        # ponytail: last entry == the address the nearest proxy saw, correct whether the
        # ingress appends or overwrites. Measure it once on the real deployment (the
        # `client=` field in the request log) and pin the index if there are two hops.
        forwarded = request.headers.get("X-Forwarded-For", "")
        if forwarded:
            last = forwarded.rsplit(",", 1)[-1].strip()
            if last:
                return last
    return get_remote_address(request)


def user_rate_limit_key(request: Request) -> str:
    """Identify the caller for rate limiting by account, falling back to network origin.

    Used for the routes that cost money — chat and the two wizards. Per-IP is the wrong unit
    there: a shared office NAT would throttle a whole company against one budget, while one
    user on a phone gets a fresh address whenever they like. The account is the thing being
    billed, so the account is the thing being limited.

    The cookie is read through ``read_token``, which verifies the signature and the expiry,
    so the key cannot be forged into someone else's bucket. Anything unusable falls back to
    the address, which fails *closed*: a signed-out caller shares the IP bucket and is about
    to be refused by the auth dependency anyway.

    Args:
        request: The incoming request.

    Returns:
        ``u:<user uuid>`` for a signed-in caller, else the client's IP address.
    """
    # Imported inside the body: `app.core.deps` imports the service layer, which imports the
    # database, and this module is imported by every endpoint module in turn.
    from app.core.deps import SESSION_COOKIE
    from app.core.security import read_token

    claims = read_token(request.cookies.get(SESSION_COOKIE))
    if claims is not None:
        return f"u:{claims[0]}"
    return rate_limit_key(request)


#: Shared across every rate-limited endpoint; also published on ``app.state.limiter``.
limiter = Limiter(key_func=rate_limit_key)

__all__ = ["limiter", "rate_limit_key", "user_rate_limit_key"]
