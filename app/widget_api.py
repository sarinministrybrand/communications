"""Endpoints the embedded widget calls.

The widget routes every request through one `API` object (web/widget.html:331),
so its contract is a fixed list of paths. These are those paths, mounted under
/v1 and tenant-scoped through the same `current_church` dependency as the
partner API -- there is no second auth path and no second place a church_id can
enter.

Shapes here match what the widget already destructures (`{groups: [...]}`,
`{messages: [...]}` and so on), because changing the widget's expectations would
mean editing 2,700 lines of working UI to no benefit.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from . import db
from .config import settings

router = APIRouter(prefix="/v1", tags=["widget"])


def _church(dep) -> int:  # indirection so api.py owns the dependency
    return dep


def register(app, current_church) -> None:
    """Attach the widget routes, reusing the app's own auth dependency."""

    @router.get("/bootstrap")
    async def bootstrap(church_id: int = Depends(current_church)):
        """First call the widget makes. Everything it needs to render a shell:
        who the church is, what it may use, and what is unread."""
        p = await db.pool()
        async with p.acquire() as conn:
            church = await conn.fetchrow(
                "SELECT id, name, long_code FROM church WHERE id = $1", church_id
            )
            if church is None:
                raise HTTPException(404, "church not found")
            unread = await conn.fetchval(
                "SELECT coalesce(sum(unread), 0) FROM conversation "
                "WHERE church_id = $1 AND state <> 'closed'",
                church_id,
            )
            people = await conn.fetchval(
                "SELECT count(*) FROM person WHERE church_id = $1", church_id
            )
        return {
            "church": {"id": church["id"], "name": church["name"]},
            # The shared short code is used when a church has no dedicated long
            # code, matching AssignedDID in the sender.
            "sender": (church["long_code"] or settings.default_short_code or "shared short code"),
            "counts": {"people": people, "unread": int(unread or 0)},
            # Channels the BACKEND can actually deliver today. The widget hides
            # what is not listed, so it stops promising email, voice and push
            # until those exist.
            "channels": ["sms"],
            "views": [
                "dashboard", "compose", "inbox", "sent", "people", "groups", "reports",
            ],
            "dev_mode": settings.dev_mode,
        }

    @router.get("/dashboard")
    async def dashboard(church_id: int = Depends(current_church)):
        p = await db.pool()
        async with p.acquire() as conn:
            # Scoped to the last 7 days so it reads one partition set, not the
            # whole delivery table.
            stats = await conn.fetchrow(
                "SELECT count(*) FILTER (WHERE status = 'delivered') AS delivered, "
                "       count(*) FILTER (WHERE status = 'sent')      AS sent, "
                "       count(*) FILTER (WHERE status = 'failed')    AS failed, "
                "       count(*) AS total "
                "FROM delivery "
                "WHERE church_id = $1 AND created_at >= now() - interval '7 days'",
                church_id,
            )
            replies = await conn.fetchval(
                "SELECT count(*) FROM inbound "
                "WHERE church_id = $1 AND received_at >= now() - interval '7 days'",
                church_id,
            )
            opted_out = await conn.fetchval(
                "SELECT count(*) FROM optout WHERE church_id = $1", church_id
            )
            recent = await conn.fetch(
                "SELECT id, body, state, reach, created_at, sent_at FROM broadcast "
                "WHERE church_id = $1 ORDER BY created_at DESC LIMIT 5",
                church_id,
            )
        return {
            "stats": dict(stats) if stats else {},
            "replies": replies,
            "opted_out": opted_out,
            "recent": [dict(r) for r in recent],
        }

    @router.get("/messages")
    async def messages(limit: int = 50, church_id: int = Depends(current_church)):
        if not 1 <= limit <= 200:
            raise HTTPException(422, "limit must be 1-200")
        p = await db.pool()
        async with p.acquire() as conn:
            rows = await conn.fetch(
                "SELECT id, body, state, reach, created_at, sent_at FROM broadcast "
                "WHERE church_id = $1 ORDER BY created_at DESC LIMIT $2",
                church_id,
                limit,
            )
        return {"messages": [dict(r) for r in rows]}

    @router.get("/messages/{message_id}")
    async def message(message_id: int, church_id: int = Depends(current_church)):
        p = await db.pool()
        async with p.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT id, body, state, reach, audience, created_at, sent_at "
                "FROM broadcast WHERE id = $1 AND church_id = $2",
                message_id,
                church_id,
            )
            if row is None:
                raise HTTPException(404, "no such message")
            counts = await conn.fetch(
                "SELECT status, count(*) AS n FROM delivery "
                "WHERE church_id = $1 AND broadcast_id = $2 GROUP BY status",
                church_id,
                message_id,
            )
        return {**dict(row), "delivery": {c["status"]: c["n"] for c in counts}}

    @router.get("/groups")
    async def groups(church_id: int = Depends(current_church)):
        p = await db.pool()
        async with p.acquire() as conn:
            rows = await conn.fetch(
                "SELECT g.id, g.name, count(m.person_id) AS count "
                "FROM grp g LEFT JOIN grp_member m ON m.group_id = g.id "
                "WHERE g.church_id = $1 GROUP BY g.id, g.name ORDER BY g.name",
                church_id,
            )
        return {"groups": [dict(r) for r in rows]}

    @router.get("/groups/{group_id}")
    async def group(group_id: int, church_id: int = Depends(current_church)):
        p = await db.pool()
        async with p.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT id, name FROM grp WHERE id = $1 AND church_id = $2",
                group_id,
                church_id,
            )
            if row is None:
                raise HTTPException(404, "no such group")
            members = await conn.fetch(
                "SELECT p.id, p.first_name, p.last_name, p.phone FROM person p "
                "JOIN grp_member m ON m.person_id = p.id "
                "WHERE m.group_id = $1 AND p.church_id = $2 "
                "ORDER BY p.last_name, p.first_name LIMIT 200",
                group_id,
                church_id,
            )
        return {**dict(row), "members": [dict(m) for m in members]}

    @router.get("/people")
    async def people(
        q: str = "", limit: int = 50, offset: int = 0,
        church_id: int = Depends(current_church),
    ):
        if not 1 <= limit <= 200:
            raise HTTPException(422, "limit must be 1-200")
        p = await db.pool()
        async with p.acquire() as conn:
            rows = await conn.fetch(
                "SELECT p.id, p.first_name, p.last_name, p.phone, p.email, "
                "  EXISTS (SELECT 1 FROM optout o "
                "          WHERE o.church_id = p.church_id AND o.phone = p.phone) AS opted_out "
                "FROM person p "
                "WHERE p.church_id = $1 "
                # Parameterised, never interpolated: this value is user input.
                "  AND ($2 = '' OR p.first_name ILIKE '%' || $2 || '%' "
                "               OR p.last_name  ILIKE '%' || $2 || '%' "
                "               OR p.phone      ILIKE '%' || $2 || '%') "
                "ORDER BY p.last_name, p.first_name LIMIT $3 OFFSET $4",
                church_id, q, limit, offset,
            )
            total = await conn.fetchval(
                "SELECT count(*) FROM person WHERE church_id = $1", church_id
            )
        return {"people": [dict(r) for r in rows], "total": total}

    @router.get("/people/{person_id}")
    async def person(person_id: int, church_id: int = Depends(current_church)):
        p = await db.pool()
        async with p.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT id, first_name, last_name, phone, email, created_at "
                "FROM person WHERE id = $1 AND church_id = $2",
                person_id,
                church_id,
            )
            if row is None:
                raise HTTPException(404, "no such person")
            groups_in = await conn.fetch(
                "SELECT g.id, g.name FROM grp g JOIN grp_member m ON m.group_id = g.id "
                "WHERE m.person_id = $1 AND g.church_id = $2",
                person_id,
                church_id,
            )
        return {**dict(row), "groups": [dict(g) for g in groups_in]}

    @router.get("/threads/{thread_id}")
    async def thread(thread_id: int, church_id: int = Depends(current_church)):
        """One conversation, with its messages. Reading it clears the unread
        count, which is why this is not a pure GET internally."""
        p = await db.pool()
        async with p.acquire() as conn:
            convo = await conn.fetchrow(
                "SELECT c.id, c.phone, c.state, c.assignee_id, c.last_at, "
                "       p.first_name, p.last_name, p.id AS person_id "
                "FROM conversation c LEFT JOIN person p ON p.id = c.person_id "
                "WHERE c.id = $1 AND c.church_id = $2",
                thread_id,
                church_id,
            )
            if convo is None:
                raise HTTPException(404, "no such conversation")
            msgs = await conn.fetch(
                "SELECT body, reply_type, received_at FROM inbound "
                "WHERE church_id = $1 AND from_phone = $2 "
                "ORDER BY received_at DESC LIMIT 100",
                church_id,
                convo["phone"],
            )
            await conn.execute(
                "UPDATE conversation SET unread = 0 WHERE id = $1 AND church_id = $2",
                thread_id,
                church_id,
            )
        return {**dict(convo), "messages": [dict(m) for m in msgs]}

    @router.get("/staff")
    async def staff(church_id: int = Depends(current_church)):
        """Assignable users. Staff accounts live in the Amplify host, not here,
        so this is empty until that lookup is wired -- the widget treats an
        empty list as "assignment unavailable" and hides the control."""
        return {"staff": []}

    app.include_router(router)
