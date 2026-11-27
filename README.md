# Church Communications — embeddable messaging platform

Church messaging as a module a partner app embeds in one iframe: compose, send,
two-way inbox, people, groups, reports. Styled to Ministry Brands Amplify, with
navigation across the **top** because Amplify already owns the left rail.

```bash
cp .env.example .env     # fill in ESENDEX_DEFAULT_LICENSE_KEY
docker compose up --build
# API        http://localhost:8000
# Widget     http://localhost:8000/embed/widget
# Readiness  http://localhost:8000/readyz
```

Run the checks without Docker (stdlib only, no install needed):

```bash
python -m app.test_core
```

---

## Embedding

```html
<script src="https://comms.example.com/embed.js"></script>
<div id="comms"></div>
<script>
  const comms = HgsComms.mount('#comms', {
    token: amplifySession.jwt,
    view: 'dashboard',
    onSent: e => amplify.toast(`Sent to ${e.reach} people`)
  });
</script>
```

Restrict what a partner sees — the nav renders only the views you pass:

```js
HgsComms.mount('#comms', { views: ['compose', 'sent', 'inbox'] });
```

Two things are deliberate here. The widget is an **iframe**, not an npm package,
so a partner's CSS cannot reach into it and a security fix ships without the
partner redeploying. And `frame-ancestors` is built from `EMBED_ALLOWED_ORIGINS`
and **refuses to serve on `*`** — a wildcard would let any site on the internet
frame a signed-in church's inbox.

---

## Architecture

```
POST /v1/broadcasts ──► broadcast row ──► comms:fanout (Redis Stream)
                                               │
                                     fanout worker
                                     ├─ expand audience (opt-out gate)
                                     ├─ COPY delivery rows
                                     └─► comms:send, 2000 phones per message
                                               │
                                        send worker ──► Esendex AdvancedSMSsend
                                                              │
Esendex postback ──► /webhooks/esendex ──► comms:status ──► status writer
```

| Piece | Choice | Why |
|---|---|---|
| API | FastAPI, stateless | Scales by replica count; all state in PG/Redis |
| Queue | Redis Streams + consumer groups | A crashed worker's batch is recoverable via `XCLAIM`; a list would lose it |
| Store | Postgres 16, `delivery` partitioned daily | Archiving a day is a `DETACH`, not a 1-billion-row `DELETE` |
| Provider | Esendex / CDYNE SOAP | Already the production provider |
| Driver | asyncpg, no ORM | The hot path is a `COPY` of millions of rows; an ORM only gets in the way |

**Split at the shape change.** Fan-out is one big DB write with no network; send
is network-bound with no big write. Separate workers mean a slow provider can't
block audience expansion, and a 200k-member audience can't starve provider calls
already in flight.

### The 1 billion/day target, honestly

| | |
|---|---|
| Average | **11,574 msg/sec** |
| Sunday-morning peak (10x) | **~115,000 msg/sec** |
| Provider calls at 2000/batch | ~500k/day — **~6/sec average** |
| `delivery` rows | 1e9/day, ~200 GB/day before compression |

The batch ratio is what makes this affordable: 1e9 messages is ~500k API calls,
not 1e9. One send worker at `SEND_CONCURRENCY=16` and ~1s per call moves ~32k
msg/sec of API throughput, so single-digit worker counts cover the average and
a few dozen cover the peak.

**The application is not the bottleneck, and scaling it will not get you to 1e9.**
The real ceilings are elsewhere:

1. **Carrier throughput on the originating number.** A 10DLC long code sends
   single-digit messages/second. A short code does ~100/sec. 115k msg/sec needs
   roughly 1,150 provisioned short codes, which is a carrier-contract and
   10DLC-registration problem, not an architecture problem.
2. **Esendex's own accepted throughput**, which is a contract number — get it in
   writing before sizing anything here.
3. **Storage.** 200 GB/day means `delivery` partitions must be detached to cheap
   storage on a schedule from day one, not when the disk fills.

For scale: US SMS traffic in total runs a few billion messages a day, and
55,000 churches sending 1e9 would be ~18,000 messages per church per day. If the
real figure is 1e9/month (~400/sec peak), this stack handles it on three workers.
I have built for 1e9/day horizontally regardless — nothing here assumes the
smaller number — but the provisioning conversation is where that target lives or
dies.

---

## What is already live, and what this adds

HGS **already does two-way SMS in production** on Esendex: inbound replies,
STOP/START, HELP, keywords, text-to-join, reply-notification email and inbound
billing all work today, handled by `cdyne_sms_response.aspx` in
`HGS-KeywordServiceWebApp`.

What does not exist is a **conversation inbox** — somewhere staff can read a
thread, answer it, assign it and close it. That is the `conversation` table and
`/v1/inbox`, and it is the only genuinely new concept in this schema.

Do not rebuild the inbound engine. Build on it.

> `HGS-MyCastWebApp/SchoolCastWebApp/callback/cdyne_sms_response.aspx.vb` is a
> stale copy with its body commented out. It does not serve production. Reading
> it and concluding inbound is unimplemented is the standard wrong turn here.

### Two traps encoded in the code

**`MessageID` vs `MatchedMessageID`.** On an inbound postback, `MessageID` is the
inbound message and `MatchedMessageID` is the outbound one it answers. The live
handler *overwrites* its `messageId` local with `MatchedMessageID` when present,
so the value that reaches `sms_inbound.message_id` is the **outbound** id. It is
not a unique key for the inbound row. `resolve_inbound_ids()` keeps them apart;
`test_inbound_id_trap` fails if anyone merges them again.

**Keyword matching is exact, never a prefix.** `"stop by the potluck at 6"` is a
reply, not an opt-out. A prefix match silently unsubscribes members and is a
compliance claim you cannot defend. `test_classify_inbound_is_exact_not_prefix`
pins this.

---

## Security

- **No secret has a default.** A blank credential fails at boot rather than
  falling back to someone's dev key. The existing HGS chain commits live
  credentials in plaintext across five repos — **rotate every one of them before
  granting any external developer repo access.** History rewriting will not help;
  anyone with a clone already has them.
- **One send-time gate.** Opt-out is checked in `audience.expand()` and nowhere
  else, so a new send path cannot forget it.
- **Idempotency-Key** on broadcast creation. Without it, a client retry after a
  timeout sends the whole church a second time.
- **Tenant isolation.** `church_id` is the first column of every index and is
  taken from the verified token, never from a request body.
- Container runs as a non-root user; webhook input is treated as hostile and
  never names a tenant.

---

## Status

| Working | Not built yet |
|---|---|
| Schema with daily partitioning | Real auth (`AUTH-HOOK` in `app/api.py`) |
| Esendex envelope, parse, retry | Status-writer loop for `comms:status` |
| Audience expansion + opt-out gate | Reconciler for lost delivery receipts |
| Broadcast create / status / reach preview | Email and voice channels |
| Webhook receiver, all three branches | Scheduled-send dispatcher |
| Inbox query | Partition archiver |
| Widget + embed SDK | Widget wired to `/v1` (still calls the prototype API) |
| Docker, compose, CI | |
| **Esendex wire format, verified live (HTTP 200)** | |

### Esendex wire format — recovered and verified

The shipping sender binds a WCF client by endpoint *name* (`sms2wsHttpBinding`),
so no repo contains the actual envelope. It was recovered from the live WSDL and
confirmed with an HTTP 200 from `sms2tls.esendex.us`. Four rejections on the way,
each now pinned by a test:

| Mistake | What the service says |
|---|---|
| SOAP 1.1 envelope, `text/xml` | `HTTP 415` — expects `application/soap+xml` |
| No WS-Addressing header | `a:ActionMismatch` |
| Action `ws.cdyne.com/sms/AdvancedSMSsend` | `ContractFilter mismatch` |
| Fields grouped logically | schema violation |

The real contract: namespace `http://sms2.cdyne.com`, action
`http://sms2.cdyne.com/Isms/AdvancedSMSsend`, wrapper parameter
**`AdvancedRequest`** (not `request`), data contract
`http://schemas.datacontract.org/2004/07/SmsWS`, and **alphabetical field
order**, because `DataContractSerializer` sorts members. Do not "tidy" that
order — `test_field_order_is_alphabetical` exists to stop exactly that.

**Auth is the gating item.** Three `AUTH-HOOK` comments mark where it belongs.
The `token` option is already plumbed through the SDK and handshake, so nothing
about the partner contract changes when it lands.

### Local Python note

This project's own checks run on bare Python, but `asyncpg` and `pydantic-core`
have no prebuilt wheels for Python 3.15 and will not `pip install` without a
compiler. Use Docker to run the service; the protocol logic is import-light on
purpose so `python -m app.test_core` works anywhere.
