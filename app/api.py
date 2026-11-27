"""Public API + embed host.

Three surfaces, each with a different trust level:

  /v1/*        partner API      -- authenticated, tenant-scoped
  /webhooks/*  provider callback -- unauthenticated by nature, so validated hard
  /embed/*     the iframe itself -- framed by third parties, so CSP-locked

Stateless: scale it by running more copies behind a load balancer. All
cross-request state is in Postgres or Redis.
"""
from __future__ import annotations

import json
import logging
import pathlib

from fastapi import Body, Depends, FastAPI, Header, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from pydantic import BaseModel, Field, field_validator

from . import db
from .audience import expand
from .config import settings
from .esendex import classify_inbound, postback_kind, resolve_inbound_ids

log = logging.getLogger(__name__)
app = FastAPI(title="HGS Church Communications", version="0.1.0")

WEB = pathlib.Path(__file__).resolve().parent.parent / "web"


# ------------------------------------------------------------------- auth
# AUTH-HOOK: replace with real token verification (JWT from the Amplify host,
# or a partner API key). Everything downstream already takes church_id from
# this one function, so the rest of the service does not change when it lands.
async def current_church(
    authorization: str | None = Header(default=None),
    x_church_id: str | None = Header(default=None),
) -> int:
    if settings.dev_mode:
        return int(x_church_id or 1)
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "missing bearer token")
    raise HTTPException(501, "token verification not implemented -- see AUTH-HOOK in app/api.py")


# ----------------------------------------------------------------- health
@app.get("/healthz", response_class=PlainTextResponse)
async def healthz() -> str:
    """Liveness only -- no dependency checks, so a brief Postgres blip does not
    get the whole fleet restarted by the orchestrator."""
    return "ok"


@app.get("/readyz", response_class=PlainTextResponse)
async def readyz() -> Response:
    """Readiness: can this instance actually serve? Checked by the LB."""
    try:
        p = await db.pool()
        async with p.acquire() as conn:
            await conn.execute("SELECT 1")
        r = await db.redis()
        await r.ping()
    except Exception as exc:
        return PlainTextResponse(f"not ready: {type(exc).__name__}", status_code=503)
    return PlainTextResponse("ready")


# -------------------------------------------------------------- broadcasts
class AudienceItem(BaseModel):
    kind: str = Field(pattern="^(group|person|all)$")
    id: int | None = None


class BroadcastIn(BaseModel):
    body: str = Field(min_length=1, max_length=1600)
    audience: list[AudienceItem] = Field(min_length=1)
    scheduled_at: str | None = None

    @field_validator("body")
    @classmethod
    def no_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("body cannot be whitespace only")
        return v


@app.post("/v1/broadcasts", status_code=202)
async def create_broadcast(
    payload: BroadcastIn = Body(...),
    church_id: int = Depends(current_church),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    """Accept a broadcast and queue it. Returns immediately -- fan-out happens
    in the worker, because expanding a 200k-member audience inside a request
    would hold the connection for minutes.

    Idempotency-Key makes a retried POST safe: the same key returns the first
    broadcast instead of sending twice. Without it a client retry on a timeout
    double-sends to the whole church.
    """
    p = await db.pool()
    async with p.acquire() as conn:
        if idempotency_key:
            existing = await conn.fetchrow(
                "SELECT id, state, reach FROM broadcast WHERE church_id = $1 AND idem_key = $2",
                church_id,
                idempotency_key,
            )
            if existing:
                return {
                    "id": existing["id"],
                    "state": existing["state"],
                    "reach": existing["reach"],
                    "deduplicated": True,
                }

        row = await conn.fetchrow(
            "INSERT INTO broadcast (church_id, body, audience, state, idem_key, scheduled_at) "
            "VALUES ($1, $2, $3::jsonb, 'queued', $4, $5) RETURNING id",
            church_id,
            payload.body,
            json.dumps([a.model_dump() for a in payload.audience]),
            idempotency_key,
            payload.scheduled_at,
        )

    await db.enqueue(db.FANOUT_STREAM, broadcast_id=row["id"], church_id=church_id)
    return {"id": row["id"], "state": "queued"}


@app.get("/v1/broadcasts/{broadcast_id}")
async def get_broadcast(broadcast_id: int, church_id: int = Depends(current_church)):
    p = await db.pool()
    async with p.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id, body, state, reach, created_at, sent_at FROM broadcast "
            "WHERE id = $1 AND church_id = $2",
            broadcast_id,
            church_id,
        )
        if row is None:
            raise HTTPException(404, "no such broadcast")
        # Status counts come from the partition for the broadcast's own day, so
        # this never scans the whole delivery table.
        counts = await conn.fetch(
            "SELECT status, count(*) AS n FROM delivery "
            "WHERE church_id = $1 AND broadcast_id = $2 GROUP BY status",
            church_id,
            broadcast_id,
        )
    return {
        **dict(row),
        "delivery": {c["status"]: c["n"] for c in counts},
    }


@app.post("/v1/broadcasts/preview")
async def preview_reach(
    payload: BroadcastIn = Body(...), church_id: int = Depends(current_church)
):
    """How many people would actually receive this, after opt-outs and blank
    numbers. Shown in the composer before the send button is armed."""
    p = await db.pool()
    async with p.acquire() as conn:
        rows = await expand(conn, church_id, [a.model_dump() for a in payload.audience])
    segments = (len(payload.body) + 152) // 153 if len(payload.body) > 160 else 1
    return {"reach": len(rows), "segments_each": segments, "segments_total": len(rows) * segments}


# ------------------------------------------------------------------ inbox
@app.get("/v1/inbox")
async def inbox(
    state: str = "open", limit: int = 50, church_id: int = Depends(current_church)
):
    """The conversation list -- the one thing production does not have today.

    Inbound SMS, keywords, opt-out and billing are already live on Esendex;
    what is missing is somewhere a staff member can read and answer a thread.
    """
    if state not in ("open", "assigned", "closed"):
        raise HTTPException(422, "state must be open, assigned or closed")
    if not 1 <= limit <= 200:
        raise HTTPException(422, "limit must be 1-200")

    p = await db.pool()
    async with p.acquire() as conn:
        rows = await conn.fetch(
            "SELECT c.id, c.phone, c.state, c.unread, c.last_at, "
            "       p.first_name, p.last_name, "
            "       (SELECT body FROM inbound i WHERE i.church_id = c.church_id "
            "          AND i.from_phone = c.phone ORDER BY i.received_at DESC LIMIT 1) AS preview "
            "FROM conversation c LEFT JOIN person p ON p.id = c.person_id "
            "WHERE c.church_id = $1 AND c.state = $2::convo_state "
            "ORDER BY c.last_at DESC LIMIT $3",
            church_id,
            state,
            limit,
        )
    return {"conversations": [dict(r) for r in rows]}


# --------------------------------------------------------------- webhooks
@app.post("/webhooks/esendex")
async def esendex_postback(request: Request):
    """Provider postback. Unauthenticated by design, so treat every field as
    hostile and never trust it to name a tenant.

    Accepts form-encoded or query params, matching the live handler's contract:
      MessageID, SMSResponse, MatchedMessageID, ReferenceID,
      FromPhoneNumber, ToPhoneNumber, Message, ResponseReceiveDate

    Returns 200 on anything parseable. A provider that gets a 4xx retries,
    and a retry storm during an outage is worse than a dropped receipt -- the
    reconciler catches genuinely lost receipts.
    """
    try:
        form = await request.form()
        params = {k: str(v) for k, v in form.items()}
    except Exception:
        params = {}
    params.update({k: v for k, v in request.query_params.items() if k not in params})

    kind = postback_kind(params)
    inbound_id, matched_id = resolve_inbound_ids(params)

    # Hand off to the worker rather than touching Postgres on the provider's
    # thread: a postback storm must not be able to exhaust the DB pool and take
    # the partner API down with it.
    await db.enqueue(
        db.STATUS_STREAM,
        kind=kind,
        inbound_id=inbound_id or "",
        matched_id=matched_id or "",
        from_phone=params.get("FromPhoneNumber", ""),
        to_phone=params.get("ToPhoneNumber", ""),
        body=params.get("Message", ""),
        reference_id=params.get("ReferenceID", ""),
        received_at=params.get("ResponseReceiveDate", ""),
    )

    if kind == "inbound":
        log.info("inbound %s from=%s class=%s", inbound_id, params.get("FromPhoneNumber"), classify_inbound(params.get("Message", "")))
    return JSONResponse({"accepted": True, "kind": kind})


# ------------------------------------------------------------------ embed
@app.get("/embed.js")
async def embed_sdk() -> Response:
    """The one script tag a partner adds. Served with a short cache so an SDK
    fix reaches partners without them redeploying."""
    js = (WEB / "embed.js").read_text(encoding="utf-8")
    return Response(
        js,
        media_type="application/javascript",
        headers={"Cache-Control": "public, max-age=300"},
    )


@app.get("/embed/widget", response_class=HTMLResponse)
async def embed_widget() -> Response:
    """The framed application.

    frame-ancestors is built from the allowlist and never contains '*'. A
    wildcard here would let any site on the internet frame a signed-in church's
    inbox and read it by overlaying their own UI -- the whole reason this
    header exists.
    """
    origins = settings.embed_allowed_origins
    if not origins:
        if not settings.dev_mode:
            raise HTTPException(
                500,
                "EMBED_ALLOWED_ORIGINS is empty. Set the partner origins that may "
                "frame this widget; refusing to serve a frameable page to any origin.",
            )
        origins = ["http://localhost:4200", "http://127.0.0.1:4200"]

    html = (WEB / "widget.html").read_text(encoding="utf-8")
    return HTMLResponse(
        html,
        headers={
            "Content-Security-Policy": "frame-ancestors " + " ".join(origins),
            "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "no-referrer",
            # X-Frame-Options has no allowlist form, so it is deliberately
            # omitted: setting it would block every partner. CSP is the control.
        },
    )
