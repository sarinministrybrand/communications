"""Unity SSO token verification for Ministry Brands hosts.

Mirrors the live flow in HGS-PWA `HGS.Core/UnityService.cs::DecryptUnitySessionKey`:

    Amplify sends a JWT
      -> read the `token` claim
      -> base64 decode it to {value, iv, mac}
      -> verify HMAC-SHA256 over (iv + value) with the shared key
      -> AES-decrypt `value` with the same key + iv  ->  Unity session key
      -> map the Unity org to a MyCast customer (our church_id)

TWO DELIBERATE DEPARTURES FROM THE SHIPPING CODE
------------------------------------------------
1. The shipping validator sets `RequireSignedTokens = false` while also setting
   `ValidateIssuer = true` and `ValidateAudience = true`. Those two settings
   contradict each other: with no signature check, `iss` and `aud` are just
   attacker-supplied strings, so validating them proves nothing. The only real
   boundary in that flow is the inner HMAC.

   Here the inner HMAC is ALWAYS enforced, and an unsigned outer JWT is refused
   unless UNITY_ALLOW_UNSIGNED=1 is set explicitly (dev only, and it logs a
   warning every time). Copying `RequireSignedTokens = false` would carry a
   known weakness into new code.

2. The shipping code compares the HMAC with `!=` on hex strings, which is not
   constant time. This uses `hmac.compare_digest`.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import logging
import os
from dataclasses import dataclass

log = logging.getLogger(__name__)


class AuthError(Exception):
    """Any verification failure. The message is for logs, never for the client:
    telling a caller which stage failed helps an attacker tune the next attempt."""


@dataclass(slots=True)
class Identity:
    org_id: str           # Unity organisation id (unity_org_customer.UnityOrgId)
    session_key: str      # decrypted Unity session key
    email: str | None = None
    user_id: str | None = None


def _b64url(seg: str) -> bytes:
    """Decode one base64url JWT segment, restoring stripped padding."""
    pad = "=" * (-len(seg) % 4)
    try:
        return base64.urlsafe_b64decode(seg + pad)
    except (binascii.Error, ValueError) as exc:
        raise AuthError(f"malformed JWT segment: {exc}") from exc


def read_jwt(token: str, *, allow_unsigned: bool, hmac_key: str | None) -> dict:
    """Return the JWT payload, verifying the signature when one is required.

    Only HS256 is handled, because that is what the shared-key Unity setup
    implies. An `alg` of `none` is refused outright -- accepting it is the
    textbook JWT bypass.
    """
    parts = token.split(".")
    if len(parts) != 3:
        raise AuthError("JWT does not have three segments")

    header = json.loads(_b64url(parts[0]) or b"{}")
    payload = json.loads(_b64url(parts[1]) or b"{}")
    alg = (header.get("alg") or "").upper()

    if alg == "NONE" or not parts[2]:
        if not allow_unsigned:
            raise AuthError(
                "unsigned JWT refused; set UNITY_ALLOW_UNSIGNED=1 only for local development"
            )
        log.warning(
            "SECURITY: accepted an UNSIGNED Unity JWT because UNITY_ALLOW_UNSIGNED=1. "
            "The outer issuer and audience claims are attacker-controlled in this mode; "
            "only the inner HMAC is trustworthy."
        )
        return payload

    if alg != "HS256":
        raise AuthError(f"unsupported JWT alg {alg!r}; only HS256 is handled")
    if not hmac_key:
        raise AuthError("signed JWT received but UNITY_SSO_KEY is not configured")

    signed = (parts[0] + "." + parts[1]).encode("ascii")
    expected = hmac.new(hmac_key.encode("ascii"), signed, hashlib.sha256).digest()
    if not hmac.compare_digest(expected, _b64url(parts[2])):
        raise AuthError("JWT signature mismatch")
    return payload


def _aes_cbc_decrypt(key: str, iv_hex: str, ciphertext_b64: str) -> bytes:
    """AES-CBC decrypt, matching UnityService.AesDecryptToString.

    `cryptography` is used rather than a hand-rolled implementation: this is a
    security primitive and writing one yourself is how padding oracles happen.
    """
    from cryptography.hazmat.primitives import padding
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    try:
        iv = bytes.fromhex(iv_hex) if len(iv_hex) == 32 else iv_hex.encode("ascii")
        ct = base64.b64decode(ciphertext_b64)
    except (ValueError, binascii.Error) as exc:
        raise AuthError(f"bad iv or ciphertext encoding: {exc}") from exc

    if len(iv) != 16:
        raise AuthError(f"AES-CBC needs a 16-byte IV, got {len(iv)}")

    decryptor = Cipher(algorithms.AES(key.encode("ascii")), modes.CBC(iv)).decryptor()
    padded = decryptor.update(ct) + decryptor.finalize()
    unpadder = padding.PKCS7(128).unpadder()
    try:
        return unpadder.update(padded) + unpadder.finalize()
    except ValueError as exc:
        raise AuthError(f"PKCS7 unpad failed: {exc}") from exc


def verify_host_token(token: str) -> Identity:
    """Verify an Amplify/Unity token and return the caller's identity.

    Raises AuthError on any failure. The caller maps `org_id` to a church_id;
    this function never decides which tenant you are, only who you are.
    """
    if not token or not token.strip():
        raise AuthError("empty token")

    key = os.environ.get("UNITY_SSO_KEY", "").strip()
    allow_unsigned = os.environ.get("UNITY_ALLOW_UNSIGNED", "0") == "1"

    payload = read_jwt(token.strip(), allow_unsigned=allow_unsigned, hmac_key=key or None)

    inner = payload.get("token")
    if not inner:
        raise AuthError("Unity JWT carries no `token` claim")
    if not key:
        raise AuthError("UNITY_SSO_KEY is not configured; cannot verify the inner HMAC")

    try:
        blob = json.loads(base64.b64decode(inner).decode("ascii", "replace"))
    except (ValueError, binascii.Error) as exc:
        raise AuthError(f"`token` claim is not base64 JSON: {exc}") from exc

    # The shipping model is camelCase-insensitive, so accept either spelling.
    def pick(*names: str) -> str:
        for n in names:
            v = blob.get(n) or blob.get(n[:1].lower() + n[1:]) or blob.get(n.capitalize())
            if v:
                return str(v)
        return ""

    value, iv, mac = pick("Value"), pick("Iv", "IV"), pick("Mac", "MAC")
    if not (value and iv and mac):
        raise AuthError("encrypted session token is missing value, iv or mac")

    # THE security boundary. Everything above this line is attacker-influenced
    # when the outer JWT is unsigned.
    computed = hmac.new(
        key.encode("ascii"), f"{iv}{value}".encode("ascii"), hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(computed.lower(), mac.lower()):
        raise AuthError("inner HMAC mismatch")

    session_key = _aes_cbc_decrypt(key, iv, value).decode("ascii", "replace").strip()
    if not session_key:
        raise AuthError("decrypted session key is empty")

    org_id = pick("OrgId", "OrganizationId", "UnityOrgId") or str(payload.get("org") or "")
    if not org_id:
        raise AuthError("no Unity organisation id in the token")

    return Identity(
        org_id=org_id,
        session_key=session_key,
        email=payload.get("email"),
        user_id=str(payload.get("sub") or "") or None,
    )
