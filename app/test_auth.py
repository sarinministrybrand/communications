"""Auth checks. This is the file to be paranoid in.

Every test here is a real bypass someone has shipped in production somewhere:
the `alg: none` trick, an unsigned token accepted because a flag was left on, a
tampered payload riding a valid-looking HMAC, and a tenant id taken from the
request instead of the token.

Run: python -m app.test_auth
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os

from app.auth import AuthError, Identity, read_jwt, verify_host_token

try:  # AES needs `cryptography`, which has no wheel for Python 3.15 yet.
    import cryptography  # noqa: F401
    HAVE_CRYPTO = True
except ImportError:  # the JWT bypass checks below still run, and they matter most
    HAVE_CRYPTO = False

KEY = "0123456789abcdef0123456789abcdef"  # 32 bytes, as AES-256 needs


def b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def make_jwt(payload: dict, *, alg: str = "HS256", key: str | None = KEY) -> str:
    head = b64url(json.dumps({"alg": alg, "typ": "JWT"}).encode())
    body = b64url(json.dumps(payload).encode())
    if alg.upper() == "NONE" or key is None:
        return f"{head}.{body}."
    sig = hmac.new(key.encode(), f"{head}.{body}".encode(), hashlib.sha256).digest()
    return f"{head}.{body}.{b64url(sig)}"


def expect(exc_type, fn, why: str):
    try:
        fn()
    except exc_type:
        return
    except Exception as e:  # noqa: BLE001
        raise AssertionError(f"{why}: raised {type(e).__name__} instead of {exc_type.__name__}") from e
    raise AssertionError(f"{why}: should have raised {exc_type.__name__}")


# ------------------------------------------------------------------ outer JWT
def test_alg_none_is_refused() -> None:
    """The textbook JWT bypass. Must never be accepted when unsigned tokens are
    not explicitly allowed."""
    tok = make_jwt({"token": "x"}, alg="none")
    expect(
        AuthError,
        lambda: read_jwt(tok, allow_unsigned=False, hmac_key=KEY),
        "alg=none",
    )


def test_unsigned_only_with_the_explicit_flag() -> None:
    tok = make_jwt({"token": "x", "sub": "u1"}, alg="none")
    # Refused by default...
    expect(AuthError, lambda: read_jwt(tok, allow_unsigned=False, hmac_key=KEY), "unsigned default")
    # ...and allowed only when someone turned it on deliberately.
    out = read_jwt(tok, allow_unsigned=True, hmac_key=KEY)
    assert out["sub"] == "u1"


def test_tampered_payload_fails_signature() -> None:
    tok = make_jwt({"token": "x", "org": "good-org"})
    head, body, sig = tok.split(".")
    evil = b64url(json.dumps({"token": "x", "org": "victim-org"}).encode())
    expect(
        AuthError,
        lambda: read_jwt(f"{head}.{evil}.{sig}", allow_unsigned=False, hmac_key=KEY),
        "payload swapped under a stolen signature",
    )


def test_wrong_key_fails() -> None:
    tok = make_jwt({"token": "x"}, key="a" * 32)
    expect(
        AuthError,
        lambda: read_jwt(tok, allow_unsigned=False, hmac_key=KEY),
        "signed with the wrong key",
    )


def test_unsupported_alg_refused() -> None:
    # RS256 header with an HMAC signature: a key-confusion attempt.
    tok = make_jwt({"token": "x"}, alg="RS256")
    expect(
        AuthError,
        lambda: read_jwt(tok, allow_unsigned=False, hmac_key=KEY),
        "RS256 header with a symmetric signature",
    )


def test_malformed_tokens() -> None:
    for bad in ("", "a.b", "a.b.c.d", "not-a-jwt", "...."):
        expect(
            AuthError,
            lambda b=bad: read_jwt(b, allow_unsigned=False, hmac_key=KEY),
            f"malformed {bad!r}",
        )


# ---------------------------------------------------------------- full flow
def _session_blob(session_key: str, *, mac: str | None = None) -> str:
    """Build the inner {value, iv, mac} blob the way the host does."""
    from cryptography.hazmat.primitives import padding
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    iv = bytes(range(16))
    padder = padding.PKCS7(128).padder()
    pt = padder.update(session_key.encode()) + padder.finalize()
    enc = Cipher(algorithms.AES(KEY.encode()), modes.CBC(iv)).encryptor()
    value = base64.b64encode(enc.update(pt) + enc.finalize()).decode()
    iv_hex = iv.hex()
    real_mac = hmac.new(
        KEY.encode(), f"{iv_hex}{value}".encode("ascii"), hashlib.sha256
    ).hexdigest()
    blob = {"Value": value, "Iv": iv_hex, "Mac": mac or real_mac, "OrgId": "org-123"}
    return base64.b64encode(json.dumps(blob).encode()).decode()


def test_happy_path() -> None:
    os.environ["UNITY_SSO_KEY"] = KEY
    os.environ["UNITY_ALLOW_UNSIGNED"] = "0"
    tok = make_jwt({"token": _session_blob("sess-abc"), "sub": "user-9", "email": "a@b.test"})
    ident = verify_host_token(tok)
    assert isinstance(ident, Identity)
    assert ident.org_id == "org-123"
    assert ident.session_key == "sess-abc"
    assert ident.user_id == "user-9"


def test_bad_inner_hmac_is_refused() -> None:
    """The inner HMAC is the real security boundary. A wrong one must fail even
    though the outer JWT signature is perfectly valid."""
    os.environ["UNITY_SSO_KEY"] = KEY
    tok = make_jwt({"token": _session_blob("sess-abc", mac="de" * 32)})
    expect(AuthError, lambda: verify_host_token(tok), "forged inner HMAC")


def test_missing_key_refuses_rather_than_skips() -> None:
    """With no shared key the HMAC cannot be checked. The only safe outcome is
    refusal -- never 'skip the check and continue'."""
    os.environ["UNITY_SSO_KEY"] = KEY
    tok = make_jwt({"token": _session_blob("sess-abc")})
    os.environ["UNITY_SSO_KEY"] = ""
    try:
        expect(AuthError, lambda: verify_host_token(tok), "no shared key configured")
    finally:
        os.environ["UNITY_SSO_KEY"] = KEY


def test_empty_and_claimless_tokens() -> None:
    os.environ["UNITY_SSO_KEY"] = KEY
    expect(AuthError, lambda: verify_host_token(""), "empty token")
    expect(AuthError, lambda: verify_host_token("   "), "whitespace token")
    expect(
        AuthError,
        lambda: verify_host_token(make_jwt({"sub": "u"})),
        "no `token` claim",
    )


# These need AES, so they need the container.
NEEDS_CRYPTO = {
    "test_happy_path",
    "test_bad_inner_hmac_is_refused",
    "test_missing_key_refuses_rather_than_skips",
}


def demo() -> None:
    tests = [(k, v) for k, v in sorted(globals().items()) if k.startswith("test_")]
    ran = skipped = 0
    for name, t in tests:
        if name in NEEDS_CRYPTO and not HAVE_CRYPTO:
            print("  SKIP " + name + " (needs cryptography; run in Docker)")
            skipped += 1
            continue
        t()
        print("  ok  " + name)
        ran += 1
    tail = (", " + str(skipped) + " skipped") if skipped else ""
    print("")
    print(str(ran) + " auth checks passed" + tail)
    if skipped:
        print("  full set: docker compose exec api python -m app.test_auth")


if __name__ == "__main__":
    demo()
