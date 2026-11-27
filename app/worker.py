"""Fan-out and send workers.

Two loops, one process, both reading Redis consumer groups so they scale by
running more copies. Split at the point where the work changes shape:

  fanout : 1 broadcast  -> N delivery rows   (one big DB write, no network)
  send   : 1 batch of 2000 -> 1 provider call (network bound, no big DB write)

Keeping them separate means a slow provider cannot block audience expansion,
and a big audience cannot starve the provider calls already in flight.

Throughput note: one send worker at SEND_CONCURRENCY=16 and ~1s per provider
call moves 16 * 2000 = 32k messages/second of *API* throughput. The binding
limit is never this code -- it is carrier throughput on the originating number.
See README.
"""
from __future__ import annotations

import asyncio
import logging
import signal

from . import db
from .audience import chunked, expand
from .config import settings
from .esendex import BATCH, EsendexClient

log = logging.getLogger(__name__)
_stop = asyncio.Event()


# ------------------------------------------------------------------ fanout
async def fanout_one(broadcast_id: int) -> int:
    """Expand one broadcast into delivery rows and queue its provider batches.

    Returns the reach. Idempotent on re-delivery: the state guard means a
    redelivered stream message is a no-op rather than a second send.
    """
    p = await db.pool()
    async with p.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                "SELECT id, church_id, body, audience, state FROM broadcast "
                "WHERE id = $1 FOR UPDATE",
                broadcast_id,
            )
            if row is None:
                log.warning("fanout: broadcast %s vanished", broadcast_id)
                return 0
            if row["state"] not in ("queued",):
                log.info("fanout: broadcast %s already %s, skipping", broadcast_id, row["state"])
                return 0

            await conn.execute(
                "UPDATE broadcast SET state = 'fanning' WHERE id = $1", broadcast_id
            )

            import json

            audience = row["audience"]
            if isinstance(audience, str):
                audience = json.loads(audience)

            recipients = await expand(conn, row["church_id"], audience)
            if not recipients:
                await conn.execute(
                    "UPDATE broadcast SET state = 'sent', reach = 0, sent_at = now() "
                    "WHERE id = $1",
                    broadcast_id,
                )
                log.info("fanout: broadcast %s had no deliverable recipients", broadcast_id)
                return 0

            # COPY, not INSERT: at a million recipients the difference is
            # minutes versus hours.
            await conn.copy_records_to_table(
                "delivery",
                columns=["church_id", "broadcast_id", "person_id", "phone", "status"],
                records=[
                    (row["church_id"], broadcast_id, r["person_id"], r["phone"], "queued")
                    for r in recipients
                ],
            )
            await conn.execute(
                "UPDATE broadcast SET state = 'sending', reach = $2 WHERE id = $1",
                broadcast_id,
                len(recipients),
            )

    # Queue provider batches outside the transaction: a Redis failure here
    # leaves rows in 'queued', which the reconciler can re-queue. Queuing
    # inside the transaction risks sending from a transaction that then
    # rolls back -- the one ordering that can double-send.
    phones = [r["phone"] for r in recipients]
    for i, batch in enumerate(chunked(phones, min(settings.send_batch_size, BATCH))):
        await db.enqueue(
            db.SEND_STREAM,
            broadcast_id=broadcast_id,
            church_id=row["church_id"],
            seq=i,
            phones=",".join(batch),
        )

    log.info("fanout: broadcast %s reach=%d batches=%d", broadcast_id, len(phones), i + 1)
    return len(phones)


# -------------------------------------------------------------------- send
async def send_one(client: EsendexClient, fields: dict) -> None:
    """Send one provider batch and record per-recipient results."""
    broadcast_id = int(fields["broadcast_id"])
    church_id = int(fields["church_id"])
    phones = [p for p in fields["phones"].split(",") if p]
    if not phones:
        return

    p = await db.pool()
    async with p.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT b.body, c.license_key, c.long_code "
            "FROM broadcast b JOIN church c ON c.id = b.church_id "
            "WHERE b.id = $1",
            broadcast_id,
        )
    if row is None:
        log.warning("send: broadcast %s vanished", broadcast_id)
        return

    # Licence key fallback chain, mirroring PrimeMessageSender:
    #   church.license_key -> platform default
    license_key = str(row["license_key"] or "") or settings.license_key_or_die()

    results = await client.send_batch(
        license_key=license_key,
        body=row["body"],
        phones=phones,
        reference_id=f"b{broadcast_id}-{fields.get('seq', 0)}",
        assigned_did=(row["long_code"] or "").strip(),
    )

    async with p.acquire() as conn:
        await conn.executemany(
            "UPDATE delivery SET provider_msg_id = $1, status = $2, error = $3, "
            "status_at = now() "
            "WHERE church_id = $4 AND broadcast_id = $5 AND phone = $6 "
            "  AND created_at >= current_date",
            [
                (
                    r.provider_msg_id,
                    "sent" if r.ok else "failed",
                    r.error,
                    church_id,
                    broadcast_id,
                    r.phone,
                )
                for r in results
            ],
        )
        # Mark the broadcast sent once nothing is left queued.
        await conn.execute(
            "UPDATE broadcast SET state = 'sent', sent_at = now() "
            "WHERE id = $1 AND state = 'sending' AND NOT EXISTS ("
            "  SELECT 1 FROM delivery d WHERE d.broadcast_id = $1 AND d.status = 'queued'"
            ")",
            broadcast_id,
        )

    failed = sum(1 for r in results if not r.ok)
    if failed:
        log.warning("send: broadcast %s batch %s had %d failures", broadcast_id, fields.get("seq"), failed)


# -------------------------------------------------------------------- loops
async def _consume(stream: str, handler, consumer: str, concurrency: int = 1) -> None:
    r = await db.redis()
    sem = asyncio.Semaphore(concurrency)

    async def run(msg_id: str, fields: dict) -> None:
        async with sem:
            try:
                await handler(fields)
            except Exception:
                # Do not ack on failure: the message stays pending and XCLAIM
                # can recover it. Acking here would silently drop a send.
                log.exception("%s: handler failed for %s", stream, msg_id)
                return
            await r.xack(stream, db.GROUP, msg_id)

    tasks: set[asyncio.Task] = set()
    while not _stop.is_set():
        try:
            batch = await r.xreadgroup(
                db.GROUP, consumer, {stream: ">"}, count=concurrency, block=2000
            )
        except Exception:
            log.exception("%s: read failed", stream)
            await asyncio.sleep(1)
            continue
        if not batch:
            continue
        for _, messages in batch:
            for msg_id, fields in messages:
                t = asyncio.create_task(run(msg_id, fields))
                tasks.add(t)
                t.add_done_callback(tasks.discard)
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    await db.ensure_streams()

    p = await db.pool()
    async with p.acquire() as conn:
        # Tomorrow's delivery partition must exist before midnight or every
        # insert after 00:00 fails. Cheap to call, catastrophic to forget.
        await conn.execute("SELECT ensure_delivery_partitions(3)")

    client = EsendexClient(settings.esendex_send_url, settings.esendex_postback_url)

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _stop.set)
        except NotImplementedError:
            pass  # Windows

    log.info("worker up: fanout + send (concurrency=%d)", settings.send_concurrency)
    try:
        await asyncio.gather(
            _consume(
                db.FANOUT_STREAM,
                lambda f: fanout_one(int(f["broadcast_id"])),
                "fanout-1",
                concurrency=4,
            ),
            _consume(
                db.SEND_STREAM,
                lambda f: send_one(client, f),
                "send-1",
                concurrency=settings.send_concurrency,
            ),
        )
    finally:
        await client.aclose()
        await db.close()


if __name__ == "__main__":
    asyncio.run(main())
