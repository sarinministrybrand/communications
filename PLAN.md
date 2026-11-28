# Church Communications Platform — Build Plan

**Status:** proposed, 2026-11-28. **Audience:** the developer(s) building it.
**Scope:** a multi-tenant messaging service (SMS/MMS first, then email) that partner
products embed with one iframe and drive through one API. People and groups come
from partner APIs; the messaging engine, consent, inbox and reporting live here.

This plan starts from the code already in this repo (see [README](README.md)), not
from zero. Section 3 says exactly what exists and what is missing.

---

## 1. Goals and non-goals

### Goals

| # | Goal | Measured by |
|---|---|---|
| G1 | Send **10,000,000 messages in one day** with zero loss and zero duplicates | Load gate in section 7 passes |
| G2 | Embeddable in any partner stack with one `<script>` tag and one iframe | A plain HTML page, a React app and a .NET WebForms page each embed it in under 30 minutes |
| G3 | People and groups are owned by the partner and fetched by API | No church re-enters or imports contacts into this product |
| G4 | Feature parity with Flocknote and Clearstream on core messaging | Parity table in section 2 |
| G5 | Styled to Amplify design standards, WCAG 2.1 AA | Design review sign-off and automated axe checks |
| G6 | Runs on Esendex/CDYNE today, with a second provider addable without touching the engine | Provider is one module behind one function signature |

### Non-goals (v1)

Giving, event signups and forms, voice calls, push notifications, a native mobile
app, and a contact database of our own. Each is a separate product decision. Do
not scaffold for them.

### The target, in numbers

The README sized for 1e9 messages/day. **That is superseded: the target is 1e7/day.**
This is a much easier and much cheaper system, and the plan deliberately does not
build for 100x more.

| | Value | Note |
|---|---|---|
| Daily volume | 10,000,000 | messages, not segments |
| Average rate | 116 msg/s | |
| Sunday peak (assume half the day's volume in 2 hours) | ~700 msg/s | |
| **Design peak** | **1,200 msg/s sustained** | about 10x average |
| **Test burst** | **2,000 msg/s** | headroom over design peak |
| Provider calls | 5,000/day at best (2,000 recipients per call) | far more in practice: one call minimum per broadcast |
| Provider postbacks | up to **20,000,000/day**, ~2,400/s at design peak | accepted + delivery receipt per message. **This is the real hot path.** |
| `delivery` storage | roughly 4 GB/day with indexes, ~360 GB for 90 hot days | estimate, confirm in Sprint 3 |

Consequence: **one Postgres primary, one Redis, and a handful of stateless
containers are enough.** No Kafka, no sharding, no Kubernetes operator work. The
scale-up path is written down in section 6 so nobody builds it early.

---

## 2. Competitive research

Sources are listed in section 11. Pricing figures come from review aggregators
that disagree with each other; treat them as directional and re-check the vendor
pages before quoting them anywhere.

| | Flocknote | Clearstream | Text In Church | Gloo Messaging |
|---|---|---|---|---|
| Core | Email + text to groups | Mass text, two-way, email | Texting + follow-up | Texting |
| Pricing model | By member count; free under ~40 members; unlimited email and standard text | By monthly credits; tiers from free to ~$949+/mo | Flat tiers, reviewers call it pricey | n/a |
| Text-to-join keywords | Yes (1 to 5 included) | Yes, unlimited on paid plans | Yes | Yes |
| Two-way replies | Members can reply to email or text | Yes, inbox | Yes | Yes |
| Automation | Scheduled reminders | Workflows (follow-up sequences) | Automated follow-up plans | n/a |
| Extras | Signups, polls, giving | Polls, subaccounts, public API | Planning Center integration | n/a |
| ChMS data | Sync/import | Integrations (Planning Center most cited) | Planning Center | n/a |
| Reviewer complaints | Long texts need add-on credits | Credit maths is confusing; long messages and high volume get expensive; members cannot reply to the group | "Does not sync right away with Planning Center"; cost | Media size limits; jargon ("broadcasts") |

### Where we win

1. **No sync, no second login.** Every competitor is a separate product that
   copies contacts out of the ChMS, and sync lag is a recurring complaint. We sit
   inside the partner product and read its people and groups directly.
2. **No credit surprises.** The compose screen shows segments, recipients after
   opt-outs, and cost before the send button is enabled. Competitors lose trust
   exactly here.
3. **A real shared inbox.** Assign, close, internal notes. Most competitors stop
   at a reply list.
4. **One engine for text and email**, one consent model, one report.
5. **Sunday-morning reliability**, proven by a published load test, not claimed.

### Parity checklist (drives phases 4 to 7)

| Feature | Phase | Feature | Phase |
|---|---|---|---|
| Mass text to groups, people, everyone | 1 (exists) | Merge fields (`{first_name}`) | 7 |
| Scheduling | 1 | Templates | 7 |
| Segment and cost preview | 4 | Link shortening and click tracking | 7 |
| MMS (image) | 4 | Automations: keyword or join triggers a sequence | 7 |
| Two-way inbox with assignment | 5 | Email broadcasts | 6 |
| Keywords and text-to-join | 5 | Multi-campus (parent/child churches) | 3 |
| Auto-replies, HELP/STOP | 5 | Roles and permissions | 3 |
| Delivery reports | 4 | Partner API and webhooks | 3 |

---

## 3. Where the repo stands today

| Area | Exists | Missing or wrong (fixed in the phase shown) |
|---|---|---|
| API | FastAPI: create/status/preview broadcast, inbox query, Esendex webhook, widget read endpoints | Write endpoints for most of the widget; partner API keys; versioned OpenAPI contract (P3) |
| Engine | Fan-out worker, send worker, Redis Streams, Esendex SOAP adapter verified live | See "engine defects" below (P1) |
| Data | Postgres schema, `delivery` partitioned by day | No migration tool; people and groups are local tables, must become a partner cache (P1, P3) |
| Auth | Unity SSO token verification | Partner-neutral embed sessions; server-to-server keys (P3) |
| Frontend | `web/widget.html` (2,700-line single file, Amplify-styled), `web/embed.js` SDK | Mostly read-only against the API; no build, tests or a11y checks (P4) |
| Ops | Dockerfile, compose, CI running stdlib tests | No deploy pipeline, metrics, alerts or load test (P0, P2) |

### Engine defects to fix first

Found by reading `app/worker.py`, `app/db.py` and `db/schema.sql`. Each is a
ticket in Sprint 1 or 2.

1. **Failed stream messages are never retried.** `_consume` reads only new
   entries (`>`) and does not ack on failure, so a failed batch sits in the
   pending list forever. Needs an `XAUTOCLAIM` loop and a dead-letter stream.
2. **A retry after a timeout can double-send.** If Esendex accepted the batch but
   the response was lost, the retry texts 2,000 people twice. Needs a provider
   idempotency answer (decision D2) or a "state unknown, reconcile before retry"
   path.
3. **`send_one` writes results with 2,000 single-row updates keyed on phone**,
   filtered by `created_at >= current_date`. That is slow, and it misses rows for
   a broadcast fanned out before midnight and sent after. Carry delivery ids and
   the partition date in the stream message; write with one `UPDATE ... FROM unnest()`.
4. **"Is the broadcast finished" is a `NOT EXISTS` scan on every batch.** Replace
   with counters on `broadcast`.
5. **Delivery receipts look up `provider_msg_id` across every daily partition.**
   At 2,400 postbacks/s over 90 partitions this is the first thing to fall over.
   Encode the partition date in `ReferenceID` so the lookup hits one partition.
6. **Fan-out loads the whole audience into memory inside one transaction.** Page
   it in `FANOUT_CHUNK` rows.
7. **Consumer names are hardcoded** (`fanout-1`, `send-1`), so replicas share one
   identity and pending ownership is ambiguous. Use hostname plus pid.
8. **No per-sender rate limit.** Nothing stops a 200k broadcast from pushing a
   long code past carrier limits.
9. **Not built at all:** status writer, receipt reconciler, scheduled-send
   dispatcher, partition archiver (already listed in the README).

---

## 4. Decisions needed before Sprint 1

These block real work. Each has a recommended default so the developer is never
idle; the owner is whoever can actually answer.

| # | Decision | Recommended default | Owner |
|---|---|---|---|
| D1 | **Sender strategy and carrier throughput.** Which numbers send 10M/day? A long code sends single-digit msg/s; a short code roughly 100 msg/s; T-Mobile caps 10DLC traffic per brand per day (2,000 to 200,000 by vetting tier). Is the shared short code approved for multi-church use? | Per-church 10DLC or toll-free numbers for normal sends, short code(s) for emergencies. Get Esendex's contracted msg/s **in writing**. | Product + Esendex account rep |
| D2 | Does Esendex de-duplicate on `ReferenceID`, and is there a status-query API? | Assume no; build reconcile-before-retry | Esendex support |
| D3 | **Partner directory contract** (section 5.3): do partners implement our spec, or do we write an adapter per partner? | Our spec, plus one adapter for Amplify/Unity as the reference | Product + first partner |
| D4 | Hosting and cloud | Ministry Brands' standard cloud. Needs only: container runtime, managed Postgres 16, managed Redis 7, object storage | Platform team |
| D5 | Does Amplify ship a component library, and in which framework? | If yes, use it. If no, keep the current no-build widget and split it into modules | Amplify design team |
| D6 | Email provider and sending domains | Amazon SES or the provider MB already has a contract with; per-church DKIM | Product + platform |
| D7 | Inbound cutover: production inbound SMS is handled today by `cdyne_sms_response.aspx` in `HGS-KeywordServiceWebApp` | Run both; legacy forwards each inbound to this service until parity is proven, then swap | HGS team |
| D8 | Who registers each church's 10DLC brand and campaign | Service does it through the provider during onboarding | Product + legal |
| D9 | Data retention | 90 days hot in Postgres, 13 months in object storage, then delete | Legal |

**Also before any external developer gets repo access:** rotate every credential
committed across the existing HGS repos (the README explains why history
rewriting does not help).

---

## 5. Architecture

### 5.1 Shape

```
 Partner app (any stack)                         Partner backend
 ┌────────────────────────┐                      ┌──────────────────┐
 │ <script embed.js>      │                      │ API key          │
 │ <iframe widget> ───────┼──── HTTPS ──┐   ┌────┤ POST /v1/...     │
 └────────────────────────┘             │   │    │ Directory API ◄──┼──┐
                                        ▼   ▼    └──────────────────┘  │
                                   ┌────────────┐                      │
                                   │  API       │ stateless, N copies  │
                                   └─────┬──────┘                      │
                 ┌───────────────────────┼─────────────────────┐       │
                 ▼                       ▼                     ▼       │
           Postgres 16             Redis Streams          Object store │
        (source of truth)       fanout / send / status   (media, archive)
                 ▲                       │                             │
                 │      ┌────────────────┼──────────────┬──────────┐   │
                 │      ▼                ▼              ▼          ▼   │
                 └─ fanout worker   send worker   status writer  sync ─┘
                                         │              ▲      worker
                                         ▼              │
                                   Esendex / CDYNE ── postbacks ──► API /webhooks
```

Rules that keep this simple:

- **Postgres is the source of truth; Redis is a work queue.** Losing Redis loses
  no messages: the reconciler re-queues any `delivery` row still `queued`.
- **Every worker is stateless and idempotent.** Scale by replica count.
- **Tenant id comes from the verified credential, never from the request body.**
- **One send-time gate** (`audience.expand`) for opt-out, consent and quiet hours.

### 5.2 Stack (keep what is here)

| Layer | Choice | Why not something bigger |
|---|---|---|
| API and workers | Python 3.12, FastAPI, asyncpg | Already written and tested; I/O bound work |
| Queue | Redis 7 Streams | 1,200 msg/s is trivial for Redis; Kafka adds an ops burden for nothing |
| Database | Postgres 16, `delivery` partitioned daily | 10M rows/day fits one primary with room to spare |
| Migrations | Numbered SQL files applied by a 30-line runner | No ORM in the project, so no Alembic |
| Widget | iframe + `embed.js` | CSS isolation; fixes ship without partner redeploys |
| Load test | k6 + a mock Esendex service | section 7 |
| Observability | OpenTelemetry to whatever D4 provides | |

### 5.3 Partner directory: people and groups by API

Partners own people and groups. We keep a **cache**, never a master copy.

Why a cache instead of live calls: an inbound text must resolve phone to person
instantly; reach preview and opt-out filtering are joins; and a partner outage at
9am Sunday must not stop a send.

**Contract a partner implements** (or an adapter translates to):

```
GET /groups?cursor=                       -> id, name, member_count, updated_at
GET /groups/{id}/members?cursor=          -> person ids
GET /people?updated_since=&cursor=        -> id, first, last, phone, email, updated_at, deleted
GET /people/{id}
Optional webhooks to us: person.updated, person.deleted, group.membership_changed
```

**Our side:**

- `directory_source` per church: base URL, credential reference, adapter name.
- Existing `person`, `grp`, `grp_member` tables gain `external_id` and
  `synced_at` and become the cache.
- Sync worker: incremental pull by `updated_since` every few minutes, plus
  webhooks where the partner supports them.
- At send time: refresh the selected groups if older than 5 minutes, then
  snapshot recipients into `delivery`. The snapshot is what was sent, forever.
- Opt-outs, consent and conversations stay here, keyed by phone or email, and
  are offered back to the partner by webhook.

### 5.4 Service API and embed auth

| Caller | Credential | Use |
|---|---|---|
| Partner backend | API key (hashed at rest, per partner, scoped) | `/v1/*`, creating embed sessions |
| Browser iframe | Short-lived embed session JWT (15 min, refreshable) minted by `POST /v1/embed/sessions` | Widget calls |
| Amplify | Existing Unity SSO token | Becomes one way to mint an embed session |
| Esendex | Shared secret in the postback URL + IP allow-list | `/webhooks/esendex` |

Embed in any stack:

```html
<script src="https://comms.example.com/embed.js"></script>
<div id="comms"></div>
<script>
  HgsComms.mount('#comms', { token: SESSION_TOKEN_FROM_YOUR_BACKEND });
</script>
```

Outbound webhooks to partners, HMAC-signed, retried with backoff:
`message.sent`, `message.delivered`, `message.failed`, `reply.received`,
`optout.created`, `optout.removed`.

### 5.5 Data model changes

| Table | Change |
|---|---|
| `partner` (new) | A product embedding us; owns API keys and allowed embed origins |
| `church` | Add `partner_id`, `timezone`, `quiet_hours`, sender numbers move to `sender` |
| `sender` (new) | Number, type (short/10DLC/toll-free), msg/s limit, daily cap, registration status |
| `person`, `grp`, `grp_member` | Add `external_id`, `synced_at`; unique on `(church_id, external_id)` |
| `consent` (new) | Per phone/email, per channel: source, timestamp, evidence. `optout` stays as the hard gate |
| `broadcast` | Add `channel`, `sender_id`, `segments`, counters (`queued`, `sent`, `delivered`, `failed`) |
| `delivery` | Add `channel`, `segments`; keep daily partitions |
| `message` (new) | One row per inbox message, inbound or outbound, so a thread is one query |
| `audit_log` (new) | Who sent what to whom, append-only |
| `webhook_endpoint`, `webhook_attempt` (new) | Outbound partner webhooks |

### 5.6 Compliance, built in rather than bolted on

- STOP/START/HELP handled on exact match only (already pinned by a test).
- Opt-out is per church and per channel; checked in one place.
- Consent record with source for every number that receives a message.
- Quiet hours by recipient or church timezone, configurable, with an explicit
  "emergency" override that is audit-logged.
- First message to a new number carries church name and opt-out instructions.
- Email: one-click `List-Unsubscribe`, physical address footer, bounce and
  complaint suppression.
- 10DLC brand and campaign registration tracked per church; unregistered
  churches cannot send from a long code.

---

## 6. Scaling path (do not build until the trigger fires)

| Trigger | Action |
|---|---|
| Postgres CPU over 60% at design peak | Read replica for reports and inbox reads |
| `delivery` hot data over ~500 GB | Shorten hot retention; archive partitions to object storage sooner |
| Sustained over 5,000 msg/s (about 50M/day) | Redis Cluster or split streams by church hash |
| Sustained over 100M/day | Shard Postgres by `church_id` (Citus or app-level); `church_id` already leads every index |
| One provider becomes a ceiling or a risk | Add a second provider module and route by sender |

---

## 7. Stress test: 10,000,000 messages in one day

Sending 10M real texts would cost tens of thousands of dollars and trip carrier
spam controls, so the gate has two parts.

### 7.1 Synthetic gate (blocks release)

**`tools/fakesendex`**: a mock provider that speaks the exact SOAP contract in
`app/esendex.py`, returns message ids, then fires the real postbacks (accepted,
delivery receipt, inbound reply) with configurable behaviour:

| Knob | Default |
|---|---|
| Response latency | 200 ms median, 3 s p99 |
| Rejected recipients | 2% |
| HTTP 5xx | 0.5% |
| Response lost after accept (the double-send case) | 0.1% |
| Delivery receipt delay | 1 to 60 s |
| Inbound reply rate / STOP rate | 1% / 0.1% |

**Seed data**: 5,000 churches with a realistic size spread (median 250 people,
largest 200,000), about 12M people, through the partner directory mock so sync is
under test too.

| Scenario | Load | Pass criteria |
|---|---|---|
| S1 Soak | 10M over 24 h on a Sunday-shaped curve | All criteria below hold for the full run; no memory growth |
| S2 Burst | 10M in 2 h (~1,400 msg/s), spikes to 2,000 | Queue lag under 60 s; API p99 under 300 ms |
| S3 Whale | One broadcast to 500,000 recipients | First message out in under 10 s; other churches' sends not delayed over 30 s |
| S4 Chaos | Kill workers mid-send, restart Redis, fail over Postgres, 5xx storm | **Zero lost, zero duplicate** after recovery |
| S5 Postback storm | 5,000 postbacks/s for 30 min | Status lag p99 under 30 s; no dropped receipts |
| S6 Inbound | 200 replies/s | Thread visible in inbox within 2 s |

Criteria on every scenario:

- **Lost = 0:** every `delivery` row reaches a terminal state.
- **Duplicates = 0:** the mock's received count per (broadcast, phone) is exactly 1.
- Opted-out numbers received = 0.
- Per-sender rate limits never exceeded (the mock enforces and reports).
- Postgres CPU under 60%, Redis memory flat.

A 1/100-scale run (100k messages) runs nightly in CI. The full run is a manual
gate at the end of Phase 2 and again before GA.

### 7.2 Live canary (truth about the provider)

With Esendex's agreement: 1,000 to 10,000 real messages to owned test numbers on
each sender type, to measure real accepted msg/s, receipt timing and error codes.
The synthetic gate proves our code; only this proves decision D1.

---

## 8. Phases and sprints

Assumptions: two-week sprints; two backend/full-stack engineers and one frontend
engineer, with part-time QA and platform support. With one developer, run the
phases in the same order and roughly triple the calendar. Estimates are planning
numbers, not commitments.

| Phase | Sprints | Outcome |
|---|---|---|
| 0 Foundations | 0 (1 week) | Decisions made, environments and pipeline exist |
| 1 Engine hardening | 1 to 2 | The engine is correct under failure |
| 2 Load proof | 3 | 10M/day gate passes on the SMS path |
| 3 Service layer | 4 to 5 | Partners can authenticate, supply people and groups, receive webhooks |
| 4 Widget and embed | 6 to 7 | A church can compose, send and read reports inside a partner app |
| 5 Two-way | 8 | Inbox, keywords, legacy inbound cutover. **Pilot starts.** |
| 6 Email | 9 to 10 | Second channel on the same engine |
| 7 Engagement | 11 | Templates, merge fields, links, automations |
| 8 Launch | 12 | Security review, DR, final load gate, GA |

Frontend work for Phase 4 can start in Sprint 4 against the OpenAPI contract
while backend finishes Phase 3.

### Phase 0, Sprint 0: foundations

| ID | Ticket | Done when |
|---|---|---|
| P0-1 | Resolve D1 to D9 or accept the defaults in writing | Decision log committed to this file |
| P0-2 | Rotate leaked HGS credentials | Old keys rejected by Esendex |
| P0-3 | Dev, staging, prod environments (D4) with managed Postgres and Redis | `readyz` green in all three |
| P0-4 | CI/CD: build image, run tests, deploy to staging on merge, manual promote to prod | A merged PR reaches staging untouched by hand |
| P0-5 | Migration runner; convert `db/schema.sql` to `db/migrations/0001_init.sql` | CI applies migrations to an empty DB and to the previous version |
| P0-6 | Secrets from the platform secret store, not `.env` | No secret in any image or repo |

### Phase 1, Sprints 1 to 2: engine hardening

Sprint 1: correctness.

| ID | Ticket | Done when |
|---|---|---|
| P1-1 | `XAUTOCLAIM` recovery loop, max attempts, dead-letter stream (defect 1) | Killing a worker mid-batch loses nothing; test proves it |
| P1-2 | Unique consumer names (defect 7) | Two replicas show two consumers in `XINFO` |
| P1-3 | Reconcile-before-retry for unknown send outcomes (defect 2, D2) | Simulated lost response produces zero duplicates |
| P1-4 | Batched result write by delivery id (defect 3) | One statement per provider batch; midnight-crossing test passes |
| P1-5 | Broadcast counters replace the `NOT EXISTS` scan (defect 4) | Counters match row counts in a 1M-row test |
| P1-6 | Paged fan-out (defect 6) | 1M-recipient fan-out under 512 MB memory |
| P1-7 | Status writer: `comms:status` to batched `delivery` updates, partition date from `ReferenceID` (defect 5) | 5,000 postbacks/s sustained locally |

Sprint 2: completeness.

| ID | Ticket | Done when |
|---|---|---|
| P1-8 | `sender` table and token-bucket rate limiter per sender in Redis (defect 8) | Mock never sees a sender exceed its limit |
| P1-9 | Fair scheduling across churches so one whale cannot starve the rest | S3 fairness criterion passes at small scale |
| P1-10 | Scheduled-send dispatcher, with cancel | Due broadcasts start within 5 s; cancel before start sends nothing |
| P1-11 | Receipt reconciler: rows stuck in `sent` past a timeout are marked `unknown` and reported | No row stays non-terminal past 72 h |
| P1-12 | Partition creator and archiver to object storage (D9) | Day 91 partition detached and archived automatically |
| P1-13 | Segment calculator (GSM-7 vs UCS-2, concatenation) used by preview and billing | Table-driven test covers emoji, accents, 160/161, 70/71 |
| P1-14 | Quiet hours and consent in the send-time gate | Gate tests cover each rule and the emergency override |
| P1-15 | Metrics and alerts: queue lag, send rate, failure rate, postback lag, DLQ depth | Dashboard exists; alert fires in a staged failure |

### Phase 2, Sprint 3: load proof

| ID | Ticket | Done when |
|---|---|---|
| P2-1 | `tools/fakesendex` mock provider with the knobs in 7.1 | Passes the same envelope tests as the real adapter |
| P2-2 | Seed generator: 5,000 churches, 12M people | Seeds staging in under 30 min |
| P2-3 | k6 scenarios S1 to S6 and a results report | One command runs any scenario |
| P2-4 | Run S2 to S6, fix what breaks | All pass criteria met |
| P2-5 | Run S1 (24 h soak) | Passes; report committed under `docs/load/` |
| P2-6 | Nightly 1/100-scale run in CI | Fails the build on lost or duplicate messages |
| P2-7 | Live canary with Esendex (7.2) | Real msg/s per sender type recorded against D1 |

### Phase 3, Sprints 4 to 5: service layer

Sprint 4: identity and API.

| ID | Ticket | Done when |
|---|---|---|
| P3-1 | `partner` table, API keys, scopes, per-key rate limits | Key rotation works with no downtime |
| P3-2 | `POST /v1/embed/sessions` and embed JWT; Unity SSO becomes one issuer | Widget works with a session token and with a Unity token |
| P3-3 | Per-partner allowed embed origins drive `frame-ancestors` | Wrong origin is refused; wildcard still refused |
| P3-4 | Roles: admin, sender, inbox-only, read-only | Each endpoint has a role test |
| P3-5 | Versioned OpenAPI spec published; this is the frontend's contract | Spec lint in CI; breaking changes fail the build |
| P3-6 | Multi-campus: parent sees and sends to child churches | Tenant-isolation test suite passes |
| P3-7 | `audit_log` on every send, export and settings change | |

Sprint 5: directory and webhooks.

| ID | Ticket | Done when |
|---|---|---|
| P3-8 | Directory contract spec (5.3) and a mock implementation | Published in the partner docs |
| P3-9 | Sync worker: incremental pull, cursors, backoff, deletes | 200k-person church syncs in under 5 min; resync is idempotent |
| P3-10 | Amplify/Unity adapter as the reference implementation | Real Amplify org's groups appear in the widget |
| P3-11 | Inbound directory webhooks | Phone change at the partner reflected in under 10 s |
| P3-12 | Send-time freshness check and recipient snapshot | Person removed 1 min before send is not texted |
| P3-13 | Outbound partner webhooks, signed, retried, with a delivery log | Partner outage of 1 h loses no events |
| P3-14 | Partner onboarding guide: embed in HTML, React, .NET | A developer outside the team embeds it in under 30 min (G2) |

### Phase 4, Sprints 6 to 7: widget and embed SDK

Sprint 6: structure and compose.

| ID | Ticket | Done when |
|---|---|---|
| P4-1 | Apply D5: Amplify component library, or split `widget.html` into modules | No view file over ~400 lines |
| P4-2 | Amplify design tokens (color, type, spacing, radius) in one place; light and dark | Design team sign-off |
| P4-3 | Compose: audience picker from partner groups, live reach after opt-outs, segment and cost preview, schedule, test-send-to-me | Send disabled until reach and cost are shown |
| P4-4 | Undo window and send confirmation for large audiences | Sends over N recipients need typed confirmation |
| P4-5 | MMS image attach, stored in object storage | Size and type validated server-side |
| P4-6 | Embed SDK: token refresh, auto-resize, events, `views` restriction, deep links | `tokenExpired` round-trip works without a reload |

Sprint 7: reports and quality.

| ID | Ticket | Done when |
|---|---|---|
| P4-7 | Sent list, broadcast detail with delivered/failed/opted-out, per-recipient status, CSV export | Numbers match the database exactly |
| P4-8 | Dashboard: volume, delivery rate, replies, opt-outs | Loads under 1 s for a 200k-member church |
| P4-9 | People and group views (read-only, from partner cache) with consent status | |
| P4-10 | Settings: sender numbers, quiet hours, staff roles, registration status | |
| P4-11 | Accessibility: keyboard, focus, contrast, screen-reader labels | axe clean in CI; manual screen-reader pass |
| P4-12 | Playwright end-to-end suite inside a host page | Compose-to-delivered path runs in CI against fakesendex |
| P4-13 | Performance budget | Widget interactive under 2 s on a mid-range phone |

### Phase 5, Sprint 8: two-way

| ID | Ticket | Done when |
|---|---|---|
| P5-1 | `message` table and thread view; reply from inbox | Reply reaches handset and thread updates live |
| P5-2 | Assign, close, reopen, internal notes, unread counts | |
| P5-3 | Live updates (server-sent events) for inbox and send progress | New reply appears without refresh in under 2 s |
| P5-4 | Keywords and text-to-join: creates consent, adds to a partner group via webhook or API | Join is visible in the partner product |
| P5-5 | Auto-replies and after-hours reply | |
| P5-6 | Legacy inbound cutover (D7): forward, compare, switch | 7 days of identical handling, then legacy handler retired |
| P5-7 | Reply notifications by email to assignee | |
| P5-8 | **Pilot:** 5 to 10 churches on real traffic | Pilot exit criteria in section 9 |

### Phase 6, Sprints 9 to 10: email

| ID | Ticket | Done when |
|---|---|---|
| P6-1 | Channel seam: extract the provider interface now that there are two | Engine has no Esendex import outside its module |
| P6-2 | Email provider adapter (D6), bounces, complaints, suppression list | Hard bounce suppresses future sends |
| P6-3 | Sending domains: per-church DKIM/SPF setup and verification, shared fallback domain | Unverified domains send from the fallback |
| P6-4 | Email compose: embed an existing editor component, do not build one | Renders correctly in Gmail, Outlook, Apple Mail |
| P6-5 | Unsubscribe (one-click header and footer link), preference page | |
| P6-6 | Open and click tracking, email reports | |
| P6-7 | Combined send: text and email in one broadcast with per-person channel preference | |
| P6-8 | Load: rerun S1 and S2 with a 50/50 channel mix | Gate passes |

### Phase 7, Sprint 11: engagement

| ID | Ticket | Done when |
|---|---|---|
| P7-1 | Merge fields with safe fallback; per-recipient bodies batched as multiple `SMSRequest`s per call | Missing first name never sends "Hi ," |
| P7-2 | Templates, per church and shared by parent | |
| P7-3 | Short links on our domain, click tracking | Redirect p99 under 50 ms |
| P7-4 | Automations v1: trigger (keyword, joined group, date) to a timed sequence of messages. Linear steps only, no visual branching builder | A first-time-guest follow-up sequence runs end to end |
| P7-5 | Recurring broadcasts | |

### Phase 8, Sprint 12: launch

| ID | Ticket | Done when |
|---|---|---|
| P8-1 | External security review and penetration test; fix findings | No open high or critical |
| P8-2 | Backup, point-in-time restore and failover drill | Restore to a new instance under 1 h, rehearsed |
| P8-3 | Final load gate: S1 to S6 on the production-sized environment | All pass; report published |
| P8-4 | Runbooks: provider outage, queue backlog, stuck broadcast, bad send recall | On-call can follow each cold |
| P8-5 | Status page, SLOs, on-call rotation | |
| P8-6 | Partner docs site: API reference, embed guide, directory contract, webhooks | |
| P8-7 | GA | |

---

## 9. Definition of done and release gates

Every ticket: tests written, CI green, migrations reversible or forward-safe,
metrics for anything on the send path, docs updated, reviewed by a second person.

| Gate | Criteria |
|---|---|
| End of Phase 2 | Section 7.1 passes on SMS; D1 answered with real numbers |
| Pilot exit (Phase 5) | 4 weeks, zero duplicate or lost sends, delivery rate within 1 point of the legacy system, no opt-out violations, pilot churches would not go back |
| GA (Phase 8) | Final load gate, security review closed, DR drill done, runbooks rehearsed |

Service-level objectives at GA: API availability 99.9%; 99% of messages handed to
the provider within 60 s of send at design peak; inbox reply visible within 5 s.

---

## 10. Risks

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Carrier and provider throughput, not our code, caps daily volume | High | High | D1 and the live canary in Sprint 3, before anything is promised |
| 10DLC registration per church is slow and manual | High | Medium | Start registrations during onboarding; toll-free fallback |
| Partner directory APIs are slow, incomplete or rate-limited | Medium | High | Cache plus incremental sync; contract agreed in Sprint 0 |
| Double-send on provider retry | Medium | High | P1-3, and the chaos scenario gates every release |
| Legacy inbound cutover breaks STOP handling | Low | High | Parallel run and comparison for 7 days (P5-6) |
| Amplify design system changes under us | Medium | Low | Tokens in one file (P4-2) |
| Scope creep toward giving, forms, voice | High | Medium | Non-goals in section 1 |

---

## 11. Sources

Competitive and carrier figures were gathered 2026-11-28 from third-party
listings; verify against vendor pages before external use.

- Flocknote: [Capterra](https://www.capterra.com/p/149433/Flocknote/), [Capterra pricing](https://www.capterra.com/p/149433/Flocknote/pricing/), [G2 pricing](https://www.g2.com/products/flocknote/pricing), [PricingSaaS](https://pricingsaas.com/companies/flocknote), [Software Advice](https://www.softwareadvice.com/church/flocknote-profile)
- Clearstream: [official pricing](https://clearstream.io/pricing), [Capterra](https://www.capterra.com/p/158103/Clearstream/), [Capterra reviews](https://www.capterra.com/p/158103/Clearstream/reviews/), [G2 pricing](https://www.g2.com/products/clearstream/pricing), [GetApp](https://www.getapp.com/nonprofit-software/a/clearstream/), [Krowdbase](https://www.krowdbase.com/software/clearstream)
- Text In Church and Gloo: [Text In Church reviews](https://capterra.com/p/142758/Text-In-Church/reviews/), [Text In Church vs Clearstream](https://www.capterra.com/compare/142758-158103/Text-In-Church-vs-Clearstream), [Gloo reviews](https://www.capterra.com/p/230260/Gloo/reviews/)
- Carrier limits: [Telnyx 10DLC rate limits](https://developers.telnyx.com/docs/messaging/10dlc/10dlc-rate-limits), [Infobip 10DLC throughput](https://www.infobip.com/docs/usa-sender-registration/10dlc/10dlc-throughput-reference), [Twilio on T-Mobile daily limits](https://support.twilio.com/hc/en-us/articles/1260804800549-T-Mobile-daily-message-limits-for-long-code-messaging-with-A2P-10DLC), [Bird throughput and daily caps](https://docs.bird.com/applications/enable-channels/sms/sms-10dlc/troughput-and-daily-caps)
