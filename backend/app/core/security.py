"""Password hashing and session tokens for the panel's operator accounts.

Two halves, both deliberately small. Passwords use stdlib scrypt — no crypto dependency —
with the cost parameters stored inside the hash, so raising them later still verifies rows
written today. Session tokens are HS256 JWTs whose claims carry the user's public uuid and
nothing else: privilege is re-read from the database on every request, because a role baked
into a token can only ever be stale, and a stale privilege claim is exactly the failure
`CLAUDE.md` names.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time

import jwt

from app.core.config import settings

N, R, P = 16384, 8, 1
SALT_BYTES = 16
KEY_BYTES = 32

# Ceiling on the work factor read back out of storage. scrypt allocates
# 128 * r * (n + p + 2) bytes, so a corrupted or hostile row has to be rejected on that
# product — per-field limits let a pair that is individually modest (n=2**20, r=32) still
# ask for ~4 GiB. Production hashes (n=16384, r=8) need 16 MiB.
MAX_MEM_BYTES = 64 * 2**20
# The memory ceiling alone does not bound TIME: n=2**22 with r=1 fits comfortably under it
# and still costs minutes of CPU per verification, which turns one login into an outage.
# Production uses n=16384; 2**20 leaves four doublings of headroom for raising the cost.
MAX_N = 2**20

#: Twelve hours. The cookie's ``max_age`` is set from this same number, so the browser stops
#: sending a token the server would refuse anyway.
TOKEN_TTL_SECONDS = 12 * 3600

#: Shortest password the system will store. Long enough that the bootstrap account on a
#: public URL is not the whole attack.
MIN_PASSWORD_CHARS = 12


def _derive(password: str, salt: bytes, n: int, r: int, p: int) -> bytes:
    """Run scrypt with explicit cost parameters."""
    return hashlib.scrypt(password.encode(), salt=salt, n=n, r=r, p=p, dklen=KEY_BYTES)


def hash_password(password: str) -> str:
    """Hash a password for storage.

    Args:
        password: The plaintext password. Never logged, never stored.

    Returns:
        ``scrypt$n$r$p$salt_hex$key_hex`` — the cost parameters travel with the hash.
    """
    salt = secrets.token_bytes(SALT_BYTES)
    return f"scrypt${N}${R}${P}${salt.hex()}${_derive(password, salt, N, R, P).hex()}"


def verify_password(password: str, stored: str) -> bool:
    """Check a password against a stored hash, in constant time.

    Args:
        password: The plaintext attempt.
        stored: A hash previously produced by :func:`hash_password`.

    Returns:
        True on a match. **False for any malformed ``stored``; never raises** — this is
        called with a dummy hash for unknown usernames, and an exception there would be a
        louder oracle than the timing difference it exists to hide.
    """
    try:
        scheme, n, r, p, salt_hex, key_hex = stored.split("$")
        n, r, p = int(n), int(r), int(p)
        if scheme != "scrypt" or n <= 0 or r <= 0 or p <= 0:
            return False
        if n > MAX_N or 128 * r * (n + p + 2) > MAX_MEM_BYTES:
            return False
        expected = bytes.fromhex(key_hex)
        actual = _derive(password, bytes.fromhex(salt_hex), n, r, p)
    except (AttributeError, TypeError, ValueError):
        return False
    return hmac.compare_digest(actual, expected)


def password_epoch(password_hash: str) -> str:
    """A short fingerprint of a stored password hash.

    Carried in the token so that changing a password invalidates every session issued
    before it. Keyed with the signing secret, so the fingerprint reveals nothing about the
    hash to anyone holding a token.

    Args:
        password_hash: The user's stored hash.

    Returns:
        16 hex characters — enough that guessing one is not a shortcut, short enough that
        it costs nothing in the cookie.
    """
    return hmac.new(
        settings.auth_secret.encode(), password_hash.encode(), hashlib.sha256
    ).hexdigest()[:16]


def issue_token(user_uuid: str, password_hash: str) -> str:
    """Mint the session token for a signed-in operator.

    Args:
        user_uuid: The user's public uuid — never the internal integer id.
        password_hash: Their stored hash, fingerprinted into the token so that changing
            the password ends every session that predates the change.

    Returns:
        A signed HS256 JWT.

    Raises:
        RuntimeError: ``AUTH_SECRET`` is unset. Login must fail loudly rather than mint
            tokens anyone could forge.
    """
    if not settings.auth_secret:
        raise RuntimeError("AUTH_SECRET is not configured")
    now = int(time.time())
    return jwt.encode(
        {
            "sub": user_uuid,
            "pw": password_epoch(password_hash),
            "iat": now,
            "exp": now + TOKEN_TTL_SECONDS,
        },
        settings.auth_secret,
        algorithm="HS256",
    )


def read_token(token: str | None) -> tuple[str, str] | None:
    """Return the uuid and password fingerprint a token proves, or None.

    Missing, expired, tampered, signed with a different secret, or asking for ``alg: none``
    all return None identically — the caller must not be able to tell them apart. Requiring
    the ``pw`` claim also fails closed on tokens minted before it existed.

    Args:
        token: Raw cookie value, or None when the cookie was absent.

    Returns:
        ``(user_uuid, password_epoch)`` when the token is valid, else None.
    """
    if not token or not settings.auth_secret:
        return None
    try:
        claims = jwt.decode(
            token,
            settings.auth_secret,
            # Pinned: never let the token choose its own algorithm.
            algorithms=["HS256"],
            options={"require": ["exp", "sub", "pw"]},
        )
    except jwt.PyJWTError:
        return None
    subject, epoch = claims.get("sub"), claims.get("pw")
    if not isinstance(subject, str) or not isinstance(epoch, str) or not subject or not epoch:
        return None
    return subject, epoch
