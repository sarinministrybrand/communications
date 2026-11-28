"""Configuration. Every value comes from the environment -- no secrets in code.

The existing HGS chain commits live credentials in plaintext across five repos
(see API-SETTINGS.md). This project does not repeat that: there are no default
values for anything secret, so a missing variable fails loudly at boot instead
of silently falling back to someone's dev key.
"""
from __future__ import annotations

import os


def _req(name: str) -> str:
    val = os.environ.get(name, "").strip()
    if not val:
        raise RuntimeError(
            f"{name} is not set. Copy .env.example to .env and fill it in; "
            "this service will not start with a blank credential."
        )
    return val


def _int(name: str, default: int) -> int:
    return int(os.environ.get(name, default))


class Settings:
    # --- storage
    database_url: str = os.environ.get(
        "DATABASE_URL", "postgresql://comms:comms@localhost:5432/comms"
    )
    redis_url: str = os.environ.get("REDIS_URL", "redis://localhost:6379/0")

    # --- provider (Esendex / CDYNE)
    esendex_send_url: str = os.environ.get(
        "ESENDEX_SEND_URL", "https://sms2tls.esendex.us/sms.svc/WS"
    )
    esendex_postback_url: str = os.environ.get(
        "ESENDEX_POSTBACK_URL", "http://localhost:8000/webhooks/esendex"
    )
    # Platform default licence key. Per-church keys live in church.license_key
    # and win over this one, mirroring PrimeMessageSender's fallback chain.
    default_license_key: str = os.environ.get("ESENDEX_DEFAULT_LICENSE_KEY", "")
    default_short_code: str = os.environ.get("ESENDEX_DEFAULT_SHORT_CODE", "")

    # --- embed
    # Hosts allowed to frame the widget. "*" is refused outright: a wildcard
    # here lets any site frame a logged-in church's inbox.
    embed_allowed_origins: list[str] = [
        o.strip()
        for o in os.environ.get("EMBED_ALLOWED_ORIGINS", "").split(",")
        if o.strip() and "*" not in o  # dropped, so an all-wildcard list refuses to serve
    ]

    # --- throughput
    # Recipients per provider call. The provider caps this at 2000.
    send_batch_size: int = _int("SEND_BATCH_SIZE", 2000)
    # Concurrent in-flight provider calls per send worker.
    send_concurrency: int = _int("SEND_CONCURRENCY", 16)
    # Rows per fan-out chunk when expanding an audience.
    fanout_chunk: int = _int("FANOUT_CHUNK", 10_000)

    dev_mode: bool = os.environ.get("DEV_MODE", "0") == "1"

    def license_key_or_die(self) -> str:
        if self.dev_mode:
            return self.default_license_key or "00000000-0000-0000-0000-000000000000"
        return _req("ESENDEX_DEFAULT_LICENSE_KEY")


settings = Settings()
