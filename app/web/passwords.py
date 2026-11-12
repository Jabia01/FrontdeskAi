"""Password hashing for admin accounts.

`hashlib.scrypt` from the standard library rather than bcrypt or argon2: it is
memory-hard, it is in Python itself, and it means one fewer native wheel to
build on Windows. Parameters are stored in the hash string, so raising the cost
later does not invalidate existing passwords - they re-verify at their own
parameters and can be re-hashed on next login.

Format: scrypt$<n>$<r>$<p>$<salt-b64>$<hash-b64>
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets

# ~16 MB of memory per hash. Costly enough to hurt an offline attacker, cheap
# enough that a login is not noticeable.
_N = 2**14
_R = 8
_P = 1
_DKLEN = 32
_SALT_BYTES = 16

_PREFIX = "scrypt"


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def _unb64(text: str) -> bytes:
    return base64.b64decode(text.encode("ascii"))


def hash_password(password: str, *, n: int = _N, r: int = _R, p: int = _P) -> str:
    if not password:
        raise ValueError("password must not be empty")
    salt = secrets.token_bytes(_SALT_BYTES)
    derived = hashlib.scrypt(
        password.encode("utf-8"), salt=salt, n=n, r=r, p=p, dklen=_DKLEN
    )
    return f"{_PREFIX}${n}${r}${p}${_b64(salt)}${_b64(derived)}"


def verify_password(password: str, stored: str) -> bool:
    """Constant-time check. Any malformed hash is a failure, never an exception.

    A raised exception here would leak - a 500 on a corrupt row tells an
    attacker the account exists.
    """
    try:
        prefix, n_s, r_s, p_s, salt_s, hash_s = stored.split("$")
        if prefix != _PREFIX:
            return False
        salt = _unb64(salt_s)
        expected = _unb64(hash_s)
        derived = hashlib.scrypt(
            password.encode("utf-8"),
            salt=salt,
            n=int(n_s),
            r=int(r_s),
            p=int(p_s),
            dklen=len(expected),
        )
    except Exception:
        return False
    return hmac.compare_digest(derived, expected)


def needs_rehash(stored: str) -> bool:
    """True when a stored hash uses weaker parameters than we now use."""
    try:
        prefix, n_s, r_s, p_s, _salt, _hash = stored.split("$")
    except ValueError:
        return True
    if prefix != _PREFIX:
        return True
    return (int(n_s), int(r_s), int(p_s)) != (_N, _R, _P)


# --------------------------------------------------------------------- one-time codes


def new_login_code() -> str:
    """Six digits, uniformly random, leading zeros allowed."""
    return f"{secrets.randbelow(1_000_000):06d}"


def hash_code(code: str) -> str:
    """SHA-256. A six-digit code is not worth scrypt - it expires in minutes
    and is attempt-limited, and a login should not cost 16 MB of memory per
    guess when the guess limit is what protects it."""
    return hashlib.sha256(code.encode("utf-8")).hexdigest()


def code_matches(code: str, stored_hash: str) -> bool:
    return hmac.compare_digest(hash_code(code), stored_hash)


# ------------------------------------------------------------------ session tokens


def new_session_token() -> str:
    """The value that goes in the cookie. 32 bytes of entropy."""
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    """Only this goes in the database, so a dump does not hand over sessions."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def new_csrf_token() -> str:
    return secrets.token_urlsafe(24)
