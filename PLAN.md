# Church Communications Platform — Build Plan

**Status:** revision 4, 2026-11-28. **Audience:** the developer(s) building it.
**Scope:** a multi-tenant messaging service (SMS first, then email) that partner
products embed with one iframe and drive through one API. People and groups come
from partner APIs; the messaging engine, consent, inbox and reporting live here.

This plan starts from the code already in this repo (see [README](README.md)).
Section 3 says what exists and what is broken.

**Revision 4** adds a design track (section 8, "Design track"), planned from
the church admin's side with the double diamond method. It adds research and
prototype tickets DT-1 to DT-12 in Sprints 0 to 9, decisions D14 and D15, admin
measures on the pilot exit gate, and a part-time designer to the assumed team.
No engine ticket changed.

**Revision 3** checks the plan against the legacy HGS source (MessagingService,
KeywordServiceWebApp, HGSCoreApi and others, read locally; nothing was run and no
production data was seen). What changed from revision 2:

- Legacy does **not** send from one short code. It resolves a sender per staff
  user, customer and product, across several short codes and three live
  providers, and operators can move customers between numbers (section 1.2, D1,
  D13, P0-13).
- Section 5.3.1's description of legacy reply attribution is now verified, with
  corrections.
- Legacy opt-out matching is looser than this plan's and has a quirk; the
  keyword list and the cutover bar changed (5.7, P5-8).
- Inbound replies carry `ReferenceID`; delivery receipts can carry
  `BackupMessageID`. Both are now handled (5.3.1, I5, P1-9).
- The `sender` table carries a licence key and provider (5.6). The opt-out
  import rules are exact instead of blanket (P1-19).

**Revision 2** incorporates an independent adversarial review and one new fact
from the product owner: **all churches (thousands) send from a single shared
short code.** What changed from revision 1:

- The shared short code, not our software, sets the daily ceiling (section 1.2).
  It also changes opt-out scope, inbound routing, keywords and fairness.
- Opt-out handling, auth fixes and the mock provider moved to the front.
- "Zero lost, zero duplicate" is now backed by a per-recipient claim design
  (section 5.2) instead of an assertion.
- Consent for partner-supplied numbers, legacy opt-out migration, metering,
  onboarding and a support console are now planned work.
- The schedule grew from 13 sprints to 17 and was re-ordered.

---

## 1. Goals, non-goals and the real ceiling

### 1.1 Goals

| # | Goal | Measured by |
|---|---|---|
| G1 | The engine processes **10,000,000 messages in one day** with zero loss and zero duplicates | Load gate in section 7 |
| G2 | Embeddable in any partner stack with one `<script>` tag and one iframe | Plain HTML, React and .NET WebForms pages each embed it in under 30 minutes |
| G3 | People and groups are owned by the partner and fetched by API | No church imports contacts into this product |
| G4 | Parity with Flocknote and Clearstream on core messaging | Parity table in section 2 |
| G5 | Amplify design standards, WCAG 2.1 AA | Design sign-off and automated axe checks |
| G6 | Runs on Esendex/CDYNE today; a second provider or sender type is addable without touching the engine | Provider is one module behind one function signature |

**Non-goals (v1):** giving, event signups and forms, voice calls, push, a native
mobile app, a contact database of our own. Do not scaffold for them.

### 1.2 The shared short code is the ceiling

The README sized for 1e9 messages/day. **That is superseded: the target is 1e7/day.**

| | Value | Note |
|---|---|---|
| Daily volume | 10,000,000 messages | carriers count **segments**; assume 1.4 segments per message, so ~14M segments |
| Average rate over 24 h | 116 msg/s | |
| Average rate over a 13-hour sending day (8am to 9pm) | 214 msg/s | |
| Sunday peak (half the day's volume in 2 hours) | ~700 msg/s | |
| **Engine design peak** | **1,200 msg/s** | what our software must sustain |
| **Engine test burst** | **2,000 msg/s** | headroom |
| Provider postbacks | up to 2 per message, ~2,400/s at design peak | accepted + delivery receipt. The engine's hot path. |
| `delivery` storage | 3 to 6 GB/day with indexes, ~360 GB for 90 hot days | estimate; confirm in Phase 2 |

**The problem.** A short code typically carries on the order of 100 msg/s (the
contracted figure for ours is unknown: decision D1). At 100 msg/s, one code
running flat out for 24 hours delivers 8.64M messages. That is **below the 10M
target before allowing for quiet hours or the Sunday peak**, which needs about
700 msg/s. No amount of engineering on our side changes this.

So "10M in one day" splits in two:

1. **Engine capacity**, which this plan builds and proves against a mock provider.
2. **Sender capacity**, which is a contract and provisioning question. The
   options, to be chosen in D1:
   - a higher contracted rate on the existing code;
   - additional short codes, with churches spread across them;
   - large churches on their own registered number (the code already supports a
     per-church `long_code`); note T-Mobile caps such numbers at 2,000 to
     200,000 segments per brand per day depending on vetting.

**What the legacy code shows (revision 3).** The product owner's "one shared
short code" is not how the legacy sender behaves, so the arithmetic above must
be redone per code once P0-13 lists what is live:

- The sender is resolved in order: the staff user's own number, the customer's
  number, the product's number, then the platform default. Each has its own
  licence key (`PrimeMessageSender.vb:63-79`).
- At least five short codes appear in code: 22383 (old default), 34166 (a
  release script moves customers to it from 22383), 29988 (SimpleChurch), 31263
  (ShelbyArena) and 39256 (HCIactive) (`cdyne_sms_response.aspx.vb:300-347`).
- Each customer is routed to Esendex, Nexmo or Sinch
  (`MessagingServiceCore.vb:1367`), and an internal failover API moves customers
  in bulk from one provider number to another and back
  (`SmsFailoverController.cs`).

This may be good news for capacity (several codes, not one) but which codes
church customers actually use is a data question, not a code question.

Until D1 is answered, design for the queue being **hours deep on a Sunday**:
fair sharing between churches, priority lanes, and an honest delivery estimate in
the compose screen are requirements, not polish.

The engine itself needs only **one Postgres primary (with a synchronous standby),
one Redis, and a handful of stateless containers.** No Kafka, no sharding.

---

## 2. Competitive research

Sources in section 11. Pricing comes from review aggregators that disagree with
each other; re-check vendor pages before quoting.

| | Flocknote | Clearstream | Text In Church | Gloo Messaging |
|---|---|---|---|---|
| Core | Email + text to groups | Mass text, two-way, email | Texting + follow-up | Texting |
| Pricing model | By member count; free under ~40 members; unlimited email and standard text | Monthly credits; tiers from free to ~$949+/mo | Flat tiers; reviewers call it pricey | n/a |
| Text-to-join keywords | Yes (1 to 5 included) | Yes, unlimited on paid plans | Yes | Yes |
| Two-way replies | Reply to email or text | Inbox | Yes | Yes |
| Automation | Scheduled reminders | Workflows | Follow-up plans | n/a |
| Extras | Signups, polls, giving | Polls, subaccounts, public API | Planning Center integration | n/a |
| Reviewer complaints | Long texts need add-on credits | Confusing credit maths; cost at volume; members cannot reply to the group | Slow sync with Planning Center; cost | Media limits; jargon |

**Where we win:** no sync and no second login (we read the partner's people
directly); no credit surprises (segments, reach and cost shown before send); a
real shared inbox with assignment; one engine and one consent model for text and
email; Sunday reliability proven by a published load test.

**Parity checklist:**

| Feature | Phase | Feature | Phase |
|---|---|---|---|
| Mass text to groups, people, everyone | 1 | Merge fields | 7, or 4 under D14 |
| Scheduling (broken today, see section 3) | 1 | Templates | 7 |
| Segment, cost and delivery-time preview | 4 | Short links and click tracking | 7 |
| MMS (only if Esendex supports it: D2) | 4 | Automations | 7 |
| Two-way inbox with assignment | 5 | Email broadcasts | 6 |
| Keywords and text-to-join | 5 | Multi-campus | 3 |
| STOP/START/HELP | **1** | Roles and permissions | 3 |
| Delivery reports | 4 | Partner API and webhooks | 3 |

---

## 3. Where the repo stands today

| Area | Exists | Gap |
|---|---|---|
| API | FastAPI: create/status/preview broadcast, inbox query, Esendex webhook, widget read endpoints | Most write endpoints; partner keys; OpenAPI contract |
| Engine | Fan-out worker, send worker, Redis Streams, Esendex SOAP request envelope verified live | Defects below. No tests for DB, worker or API |
| Inbound | Webhook classifies and queues postbacks | **Nothing consumes the queue. STOP is not recorded anywhere.** |
| Data | Postgres schema, `delivery` partitioned by day | No migrations; people and groups are local tables |
| Auth | Unity SSO token verification | Tokens never expire; org id is outside the HMAC (defects A1, A2) |
| Frontend | `web/widget.html` (2,700 lines), `web/embed.js` | Mostly read-only; embed defaults are wrong (E1 to E4) |
| Ops | Dockerfile, compose, CI | CI does not run `app.test_auth`; no deploy, metrics, alerts, load test |

### Defects to fix (each maps to a ticket in section 8)

**Send path**

| # | Defect | Ticket |
|---|---|---|
| S1 | `send_one` sends whatever the queue message lists without checking whether those recipients were already sent. Any redelivery texts people twice | P1-2 |
| S2 | The in-client retry after a timeout already double-sends if Esendex accepted the batch | P1-3 |
| S3 | Transport errors and 5xx become a permanent `failed`, are acknowledged, and are never retried | P1-3 |
| S4 | Handler exceptions leave the message pending forever; nothing reclaims it | P1-4 |
| S5 | The API commits the broadcast, then enqueues to Redis outside the transaction. If Redis fails the broadcast is stuck `queued` forever, and a retry with the same idempotency key reports "deduplicated" and never sends | P1-5 |
| S6 | Fan-out that crashes partway through enqueueing never queues the remaining batches | P1-5 |
| S7 | `scheduled_at` is ignored: every broadcast is queued immediately | P1-5 |
| S8 | Idempotency is check-then-insert; two concurrent retries produce a 500 | P1-5 |
| S9 | Result writes are 2,000 single-row updates keyed on phone with no index on phone (quadratic), filtered by `created_at >= current_date` (wrong across midnight) | P1-2 |
| S10 | "Is the broadcast finished" is a `NOT EXISTS` scan per batch that cannot use the index | P1-2 |
| S11 | Fan-out loads the whole audience in memory in one transaction; `FANOUT_CHUNK` is defined and unused | P1-6 |
| S12 | Consumer names are hardcoded, and compose already runs two replicas | P1-4 |
| S13 | Redis streams are never trimmed | P1-4 |
| S14 | No rate limit, fairness or priority on the shared short code | P1-11, P1-12 |
| S15 | Opt-out is checked once at fan-out, not when each batch is sent | P1-13 |
| S16 | No circuit breaker; a 600-second timeout times 16 concurrent calls stalls every send during a provider brownout | P1-3 |
| S17 | Partitions are created only at worker start, 3 days ahead | P1-16 |
| S18 | `Concatenate` and the Unicode test are wrong: any non-ASCII character is treated as UCS-2 (overbills é, £, ü), and a 71 to 160 character emoji message is sent with `Concatenate=false` | P1-17 |
| S19 | The response parser is tested only against a SOAP 1.1 fixture in the old namespace. Whether the live response returns one result per phone, in order, is unverified | P0-9 |

**Inbound and postbacks**

| # | Defect | Ticket |
|---|---|---|
| I1 | Nothing reads `comms:status`; nothing writes `optout` or `inbound` | P1-7, P1-9 |
| I2 | STOP list lacks "revoke" and "opt out" (with a space) | P1-7 |
| I3 | Webhook is unauthenticated and logs phone numbers | P1-8 |
| I4 | No handling for postbacks that arrive before the send result is written, or out of order. Legacy confirms this happens: its handler sleeps 60 s and retries when the id is not stored yet | P1-9 |
| I5 | `BackupMessageID` is ignored. Esendex can retry a message under a new id and report the original through this field; legacy maps it back (`cdyne_sms_response.aspx.vb:158-190`). Without it those receipts match nothing | P1-9 |

**Auth and embed**

| # | Defect | Ticket |
|---|---|---|
| A1 | Unity tokens are accepted with no expiry check | P0-7 |
| A2 | `OrgId` is read from a blob the HMAC does not cover; with `UNITY_ALLOW_UNSIGNED=1` any valid token can name any church | P0-7 |
| E1 | `embed.js` defaults to the partner's own origin and to `/embed`; the server serves `/embed/widget`. The documented snippet loads a 404 | P4-6 |
| E2 | `EMBED_ALLOWED_ORIGINS=*` is passed straight into `frame-ancestors` despite the comment saying it is refused | P0-8 |
| E3 | The widget accepts `init` from any origin and takes its API address from that message | P0-8 |
| E4 | The iframe requests microphone permission for no reason | P4-6 |

---

## 4. Decisions needed

Each has a default so work is never blocked. D1, D10 and D11 change the product
and must be answered by the end of Sprint 0.

| # | Decision | Default | Owner |
|---|---|---|---|
| D1 | **Sender capacity.** Contracted msg/s on the shared short code, in writing. Is one code for thousands of churches approved by the carriers under the current rules? (Carriers moved against shared short codes from 2018 to 2021; ours may be an approved exception, but that needs to be in writing.) Which of the three options in 1.2 gets us to 10M? **Ask per code: P0-13 lists the codes church customers are on today** | Keep the shared code; add codes or dedicated numbers for the largest churches | Product + Esendex account rep |
| D2 | Esendex behaviour: does it de-duplicate on `ReferenceID`? Is there a status-query API? Does it pace delivery within one 2,000-recipient request, and at what rate? Does it support MMS? Does it block a handset at code level after STOP? How does it choose `MatchedMessageID` and the `ReferenceID` it returns on an inbound reply on a shared code? When does it send `BackupMessageID`? Can it sign or add a secret to postbacks (legacy has no check at all)? | Assume no to all; design in 5.2 works either way | Esendex support |
| D3 | Partner directory contract (5.4): partners implement our spec, or we write adapters | Our spec plus an Amplify/Unity reference adapter. Confirm Amplify has a people and groups API. Note the legacy partner API works the other way round: partners **push** contacts in (`ContactsController`), so existing partners have push integrations, not a directory to pull from | Product + first partner |
| D4 | Hosting | MB's standard cloud. Needs: container runtime, managed Postgres 16 **with synchronous standby**, managed Redis 7, object storage. Pin the instance sizes used for load gates | Platform team |
| D5 | Does Amplify ship a component library, and in which framework | If yes, use it; if no, keep the no-build widget and split it into modules | Amplify design team |
| D6 | Email provider and sending domains | Amazon SES or MB's existing provider; per-church DKIM | Product + platform |
| D7 | Inbound cutover from `cdyne_sms_response.aspx` in `HGS-KeywordServiceWebApp` | Legacy forwards every inbound here; opt-outs sync both ways for the whole parallel run; then swap. The handler already has a switch (`PushCdyneSmsToQueue`, off today) that copies each Esendex callback to a Service Bus topic; the forward may be a config change plus a subscriber | HGS team |
| D8 | Are Amplify's Unity tokens signed in production | Require signed; refuse to boot with `UNITY_ALLOW_UNSIGNED=1` outside dev | Amplify team |
| D9 | Retention | Message bodies and delivery detail: 90 days hot, 13 months archived. **Consent, opt-outs, audit log and who/when/sender send records: 5 years** (texting claims can be brought years later; confirm the period) | Legal |
| D10 | **Partner-supplied numbers with no consent evidence** | The partner contract carries `sms_consent {status, source, captured_at}`. Numbers without it are excluded from broadcasts and shown as "needs consent". Whether a one-time opt-in invite is allowed, and whether churches migrating from the legacy system are grandfathered, is a legal call | Product + legal |
| D11 | **Opt-out scope on a shared code.** A person who texts STOP to the shared code cannot say which church they mean | STOP opts the handset out of **every** church on that code; START reverses it. This matches the legacy table (`sms_optout`: handset and code only, verified), which has no church column. A legacy row with code 0 blocks the handset on every sender. Legal to confirm | Product + legal |
| D12 | **Reply from a member of two churches on the shared code** (5.3.1 rule 5) | Hold and ask the sender which church. Alternative: offer the full inbox only to churches with a dedicated number, as the legacy handover recommends. Choose once P0-12 shows how many handsets are affected | Product |
| D13 | **Churches not on Esendex.** Legacy routes each customer to Esendex, Nexmo or Sinch, and failover moves them between providers | Migrate only churches whose sender is an Esendex number. Churches on Nexmo or Sinch move to an Esendex sender at migration, or wait. Their opt-outs are imported regardless (all providers write the same `sms_optout` table) | Product + HGS team |
| D14 | **Merge fields before the pilot.** Legacy already puts first name, last name and three custom fields into a text (`PrimeMessageSender.vb:105-112`); this plan has merge fields in Phase 7, after the pilot, so churches moving from legacy would lose them | Decide from DT-4 usage data. If pilot candidates use them, move P7-1 into Sprint 9 (about a week of Phase 4 time) | Product |
| D15 | **Per-recipient translation.** Legacy translates a message into each recipient's language. This plan neither includes it nor lists it as a non-goal | Decide from DT-4 usage data. Default: non-goal for v1, stated to pilot churches up front | Product |

Before any external developer gets repo access: rotate every credential
committed across the HGS repos, **coordinated with the HGS team** so the live
sender gets the new keys in the same change.

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
        (source of truth)      (wake-up signals only)   (media, archive)
                 ▲                       │                             │
                 │   ┌──────────┬────────┼─────────┬───────────┐       │
                 │   ▼          ▼        ▼         ▼           ▼       │
                 └ dispatcher fanout   send     inbound +    sync ─────┘
                                        │      status writer
                                        ▼           ▲
                                  Esendex / CDYNE ──┴── postbacks ──► API /webhooks
```

### 5.2 How "zero lost, zero duplicate" is actually achieved

Revision 1 asserted this. The mechanism:

1. **Postgres is the queue of record.** Redis only wakes workers up. Losing
   Redis loses nothing.
2. **Outbox for broadcasts.** The API only inserts the broadcast. A dispatcher
   polls for broadcasts that are due and not started, so there is no
   commit-then-enqueue gap (fixes S5 to S8).
3. **Per-recipient claim.** Each `delivery` row moves through
   `queued → claimed (batch_id, attempt, lease_until) → submitted → accepted →
   delivered | failed | rejected`. A send worker claims rows with
   `UPDATE ... WHERE status = 'queued' ... RETURNING` and sends only the rows it
   got back. Two workers can never send the same row.
4. **Unique `ReferenceID` per attempt**, carrying the partition date and batch id.
5. **Unknown outcomes are resolved, not guessed.** If a call times out, the rows
   stay `submitted`. The worker waits a few minutes for Esendex's "accepted"
   postbacks for that reference, then re-queues only recipients that got none.
   (If D2 says Esendex de-duplicates or has a status API, use that instead.)
6. **Transient failures retry per row** with a capped attempt count; only
   provider rejections are terminal.
7. **Expired leases are re-queued** by the reconciler. It never touches rows
   that are legitimately waiting in a long queue.
8. **Statuses only move forward.** A late "sent" never overwrites "delivered".
   Postbacks for an id we have not stored yet are parked and retried.
9. **Synchronous standby** so a failover cannot resurrect already-sent rows.

Residual risk, stated honestly: if Esendex accepts a batch, we never get the
response, **and** its postbacks are also lost, those recipients get a second
copy. The load gate measures this rate; it should be zero in practice.

### 5.3 Living on one shared short code

| Concern | Design |
|---|---|
| Throughput | One global token bucket per sender in Redis, set to the contracted rate. Batch size is derived from the rate, not fixed at 2,000 (pending D2 on pacing) |
| Fairness | Weighted round-robin across churches with work waiting. A 500k broadcast gets its share, not the whole code |
| Priority lanes | Emergency, then one-to-one inbox replies, then broadcasts. A staff reply never waits behind a mass send |
| Honesty | Compose shows an estimated delivery window from current queue depth. Reports show queued time |
| Identification | Every message from the shared code starts with the church name. The segment counter includes it |
| Inbound routing | Never a silent guess. Rules in 5.3.1 |
| Keywords | Unique across **all** churches on the code. Allocation and collision handling in the service |
| Opt-out | Keyed by `(sender, phone)`, taken from the number the person actually texted (`ToPhoneNumber`), never inferred (D11). When a church is moved to another sender, its handsets' opt-outs are copied to the new sender in the same change. Legacy does not do this: after a failover, opt-outs filed against the old number stop applying |
| Growth | `sender` is a table. Adding a second code or a church's own number is data, not code |

#### 5.3.1 Attributing a reply on the shared code

SMS has no threading: a reply to a shared code carries the handset and the code,
nothing that names the church. Any attribution is an inference, so the rules
make the inference explicit and refuse to guess when it is unsafe.

**What the legacy system does** (verified in revision 3 against
`HGS-KeywordServiceWebApp`, `AppCode/utilities.vb:753-804` and
`cdyne_sms_response.aspx.vb:274-515`; the Nexmo, Sinch and Twilio handlers call
the same functions). `getLastQIdForPhoneNumber` takes the most recent
**two-way** outbound row for that handset, from any customer with two-way
enabled, with no time window. The filter by owning account is commented out.
The number that was texted is used only to look up a staff user's own number.
Consequences to avoid:

- A member of two churches has replies filed under whichever church last sent a
  two-way message, not the one being answered. A private reply can land in the
  wrong church's hands.
- A reply can attach to a message from years ago.
- A staff user's own number is scoped to that **user**, so a reply to a
  colleague's message does not thread. A **church-level** number gets no filter
  at all, so a reply to a church's own number can still attach to another
  church's message.
- A reply to a message without two-way ticked skips that message and attaches
  to an older two-way message if one exists, possibly another church's. Only if
  there is none is it stored against nobody, and the sender gets a one-time
  HELP text.
- A STOP to a known short code is filed against that code correctly. Only when
  the texted number is not a known code (Esendex's long-code pool) does legacy
  infer it: from the `ReferenceID`, else from the most recent alert to that
  handset, else it files code 0, which blocks every sender.

**Rules here**, applied in order, with `church_id` and `sender_id` written on the
inbound row at the moment it arrives:

1. **STOP, START, HELP:** apply to the number that was texted. No attribution needed.
2. **Dedicated number:** the church that owns the number, whoever sent the message.
3. **Keyword:** the church that owns that keyword on that code.
4. **Shared code, exactly one church** texted this handset from the code in the
   last 14 days: that church. (Window is a setting; tune it with P0-12 data.)
5. **Shared code, more than one church** in the window: hold the message and
   send one prompt, "Which church is this for? Reply 1 for A, 2 for B". The
   answer routes the held message. No answer in 24 hours: platform triage (D12).
6. **No church in the window:** platform triage, plus a generic reply naming the
   service and how to opt out.

Esendex sends two hints on an inbound reply: `MatchedMessageID` and the
`ReferenceID` of the message it matched. Our `ReferenceID` carries the batch id,
so it names a broadcast and therefore a church. Legacy reads it but does not use
it for attribution. Both remain hints: the provider faces the same ambiguity,
and how it picks a match is unknown (D2). Store both on the inbound row, measure
in P0-12 how often they agree with rule 4, and only then decide whether they may
break a rule 5 tie instead of prompting.

### 5.4 Partner directory: people and groups by API

Partners own people and groups; we keep a cache. A cache is needed because an
inbound text must resolve phone to person instantly, reach preview is a join, and
a partner outage on Sunday morning must not stop a send.

**Contract:**

```
GET /groups?cursor=                          -> id, name, member_count, version
GET /groups/{id}/members?cursor=&since=      -> person ids, removed ids
GET /people?updated_since=&cursor=           -> id, first, last, phone, email,
                                                sms_consent{status,source,captured_at},
                                                timezone?, updated_at, deleted
GET /people/{id}
Optional webhooks to us: person.updated, person.deleted, group.membership_changed
```

**Rules:**

- Incremental sync every few minutes; periodic full reconciliation to catch
  deletes from partners that keep no tombstones.
- At send time: try a refresh with a 3-second budget, otherwise use the cache and
  record its age on the broadcast. "Removed one minute ago is not texted" is
  guaranteed only for partners that send webhooks.
- Two people may share a phone (households). No unique index on phone; audience
  expansion de-duplicates on phone.
- The recipient snapshot in `delivery` is the record of what was sent, kept for
  the D9 retention period.
- Consent, opt-outs and conversations live here and are offered back by webhook.
- Cached personal data is encrypted at rest, covered by a data processing
  agreement with each partner, and deleted on a partner erasure request.
- Recipient timezone: partner value, else inferred from area code, else church.

### 5.5 Service API and embed auth

| Caller | Credential |
|---|---|
| Partner backend | API key (hashed at rest, scoped, per-key rate limit) |
| Browser iframe | Embed session JWT, 15 minutes, minted by `POST /v1/embed/sessions`, **carrying the allowed parent origins** |
| Amplify | Unity SSO token, exchanged for an embed session; expiry enforced; org id taken from signed or decrypted content only |
| Esendex | Secret in the postback URL (rotated), IP allow-list, and `ToPhoneNumber` must be a known sender. STOP and START get these checks without exception. Whether Esendex can sign postbacks is in D2; the legacy handler checks nothing |

```html
<script src="https://comms.example.com/embed.js"></script>
<div id="comms"></div>
<script>
  HgsComms.mount('#comms', { partner: 'acme', token: SESSION_TOKEN_FROM_YOUR_BACKEND });
</script>
```

- `embed.js` loads the widget from **its own** origin at `/embed/widget?partner=`,
  so the server can set `frame-ancestors` for that partner before any token arrives.
- The widget checks `event.origin` against the origins in the token and never
  takes an API address from a message.
- Live updates use fetch streaming with the bearer token, not `EventSource`
  (which cannot send headers) and not cookies (blocked in third-party frames).
- Outbound partner webhooks are HMAC-signed and retried. **Default is one summary
  event per broadcast** plus `reply.received` and `optout.*`. Per-recipient
  delivery events are opt-in, because at full volume they are 20M calls a day.

### 5.6 Data model changes

| Table | Change |
|---|---|
| `partner` (new) | Owns API keys and allowed embed origins |
| `church` | Add `partner_id`, `timezone`, `quiet_hours`, `status` (active/suspended) |
| `sender` (new) | Number, type, provider, **licence key** (legacy pairs every number with its own key; replaces `church.license_key`), msg/s limit, daily cap, shared or dedicated |
| `person`, `grp`, `grp_member` | Add `external_id`, `synced_at`; drop unique phone |
| `consent` (new) | Per phone or email, per channel: status, source, timestamp, evidence |
| `optout` | Add `sender_id` scope and `source` (keyword, staff, legacy import, partner) |
| `broadcast` | Add `channel`, `sender_id`, `priority`, `segments`, counters, `directory_age` |
| `delivery` | Add claim columns (`batch_id`, `attempt`, `lease_until`), `segments`, `channel` |
| `message` (new) | One row per inbox message, inbound or outbound |
| `keyword` | Unique per sender, not per church |
| `usage_daily` (new) | Segments and messages per church per day: metering and dashboards |
| `audit_log` (new) | One row per action (send, export, settings), not per recipient |
| `webhook_endpoint`, `webhook_attempt` (new) | Outbound partner webhooks |

### 5.7 Compliance, built in

- **Opt-out keywords:** stop, stopall, stop all, quit, end, revoke, opt out,
  optout, cancel, unsubscribe, plus the three legacy accepts today: unsub,
  block, arret. Matched on the whole message after trimming punctuation.
  **START:** start, resume, unstop. **HELP:** help, hlp, aide, info. Messages
  that look like an opt-out but are not exact ("please stop texting me") are
  flagged in the inbox for a human to action within a business day. Every
  opt-out gets a confirmation.
- **Known differences from legacy** (`getReplyType`,
  `cdyne_sms_response.aspx.vb:584-606`). Legacy matches by prefix, so "stopping
  by later" opts a person out; we flag it instead. If the message contains a
  space, legacy tests only the text after the first space, so "STOP ALL" is
  **not** an opt-out in legacy and "please stop" is. We honour "stop all" and
  flag "please stop". These differences are expected in P5-8.
- Opt-out and quiet hours are checked **when each batch is sent**, not only at
  fan-out.
- Quiet hours by recipient timezone. A send that reaches quiet hours pauses and
  resumes; the "emergency" override is explicit and audit-logged.
- No broadcast to a number without a consent record (D10).
- Legacy opt-outs are imported before the first real send and kept in sync
  until the legacy handler is retired.
- Compromised-account controls: per-church daily send ceiling, link and content
  screening, one-click suspend.
- Email: SPF, DKIM, DMARC alignment, one-click unsubscribe header, complaint-rate
  monitoring, domain warm-up, physical address footer.

---

## 6. Scaling path (do not build until the trigger fires)

| Trigger | Action |
|---|---|
| Sender queue routinely over an hour deep | More sender capacity (D1). This will fire long before anything below |
| Postgres CPU over 60% at design peak | Read replica for reports and inbox reads |
| `delivery` hot data over ~500 GB | Shorter hot retention |
| Sustained over 5,000 msg/s (over 400M/day) | Split work by church hash across Redis instances |
| Beyond that | Shard Postgres by `church_id`. Add `church_id` to the index prefixes that lack it first |

---

## 7. Stress test

Sending 10M real texts would be very costly and would trip carrier spam
controls, so the gate has two parts.

### 7.1 Synthetic gate (blocks release)

**`tools/fakesendex`**: a mock that speaks the SOAP contract in `app/esendex.py`,
enforces a configurable msg/s per sender, and fires real postbacks.

| Knob | Default |
|---|---|
| Response latency | 200 ms median, 3 s p99 |
| Rejected recipients | 2% |
| HTTP 5xx, batch **not** accepted | 0.4% |
| HTTP 5xx or timeout, batch **accepted** | 0.1% |
| Postback lost | 0.05% |
| Postback before response, or out of order | 1% |
| Delivery receipt delay | 1 to 60 s |
| Inbound reply / STOP rate | 1% / 0.1% |

Seed: 5,000 churches (median 250 people, largest 200,000), about 12M people,
loaded straight into the cache tables. Rerun through the directory mock in Phase 3.

| Scenario | Load | Pass criteria |
|---|---|---|
| T1 Engine soak | 10M over 24 h, sender limit set high | All standing criteria; memory within 10% of hour-1 level at hour 24 |
| T2 Engine burst | 10M in 2 h, spikes to 2,000 msg/s | Rows claimed within 60 s of the limiter allowing them; API p99 under 300 ms |
| T3 **Realistic Sunday** | 10M offered, shared code capped at the contracted rate from D1 | No church waits more than twice its fair share; inbox replies sent within 10 s; emergency sends start within 10 s; compose estimate within 20% of actual; quiet-hours pause and resume correct |
| T4 Whale | One 500,000-recipient broadcast alongside normal traffic | Other churches' throughput drops by no more than their fair share |
| T5 Chaos | Kill workers mid-send, restart Redis, fail over Postgres, provider brownout | Standing criteria hold after recovery |
| T6 Postback storm | 5,000 postbacks/s for 30 min | Status lag p99 under 30 s |
| T7 Inbound | 200 replies/s, including STOPs mid-broadcast | Reply in inbox within 5 s; no message to a handset more than 60 s after its STOP |

Standing criteria, every scenario:

- **Lost = 0.** Every row ends `delivered`, `failed` after retries, or
  `rejected` by the provider. Nothing ends `unknown`, `claimed` or `submitted`.
  Rejected count matches the mock's.
- **Duplicates:** the mock received each (broadcast, phone) at most once, except
  the documented residual case in 5.2, which is counted and reported.
- Opted-out and no-consent numbers received: 0.
- The mock never saw a sender exceed its rate.
- Postgres CPU under 60% on the instance size pinned in D4; Redis memory flat.

A 1/100-scale run is in CI nightly. Full runs are manual gates.

### 7.2 Live canary

With Esendex's agreement: 1,000 to 10,000 real messages to owned test numbers,
to measure the real accepted rate, receipt timing, error codes, and the real
shape of the response (defect S19). The synthetic gate proves our code; only this
proves D1.

---

## 8. Phases and sprints

Two-week sprints. Assumed team: two backend/full-stack engineers, one frontend
engineer, part-time QA and platform, and **a part-time designer/researcher for
the design track** (new in revision 4; without one, the frontend engineer runs
it and Phase 4 starts later). Estimates are planning numbers.

**One developer cannot deliver this plan on anything like this calendar.** With
one person, build Phases 0 to 5 only (SMS, embed, inbox), expect roughly a year,
and treat email and automations as a second project. External lead times
(Esendex answers, legal, security review) do not shrink with headcount, so start
them in Sprint 0 regardless.

| Phase | Sprints | Outcome |
|---|---|---|
| 0 Foundations | 0 | Decisions, environments, urgent security fixes |
| 1 Engine correctness | 1 to 3 | Correct under failure; STOP works; shared-code fairness |
| 2 Load proof | 4 to 5 | Section 7 passes on SMS |
| 3 Service layer | 6 to 7 | Partners authenticate, supply people, receive webhooks; metering |
| 4 Widget and embed | 8 to 9 | Compose, send and reports inside a partner app |
| 5 Two-way and migration | 10 to 11 | Inbox, keywords, legacy migration and cutover, support console. **Pilot starts.** |
| 6 Email | 12 to 13 | Second channel. One engineer stays on pilot feedback |
| 7 Engagement | 14 to 15 | Templates, merge fields, links, automations |
| 8 Launch | 16 | Security review, DR drill, final load gate, GA |

Frontend work for Phase 4 can start in Sprint 6 against the OpenAPI contract
**and the prototypes that passed testing in the design track**.

### Design track: the church admin's side (Sprints 0 to 9, alongside the engine)

The phases above are engine-first: a church admin sees nothing usable until
Sprint 8, and no step asks them what they need. This track runs in parallel and
uses the double diamond: diverge to find the right problem, converge on it,
diverge on solutions, converge on what ships.

**Who it is for.** The office administrator or communications lead. Not
technical. Sends a few texts a week, mostly Thursday to Sunday morning, and
takes the phone call when a text reaches the wrong people or keeps arriving
after someone texted STOP.

**Nobody has been interviewed yet.** The five problems below are hypotheses
from the legacy code and competitor reviews. Discover exists to confirm, reorder
or drop them:

1. Will it get there on time? A Sunday 8am text that lands at 11 is worthless.
2. Did I pick the right people? Who and how many, before pressing send.
3. Who replied, and has anyone answered? Replies are pastoral, sometimes private.
4. Am I safe? Nobody who opted out or never agreed gets a text.
5. What will it cost? No surprises from long messages.

**Diamond 1: the right problem.**

| ID | Stage | Sprint | Ticket | Done when |
|---|---|---|---|---|
| DT-1 | Discover | 0 | Recruit 8 to 10 church admins, including two large and two multi-campus churches. These are also the pilot candidates | Sessions booked |
| DT-2 | Discover | 0 to 1 | Interviews: what they send, when, and what went wrong last time | Notes for every session |
| DT-3 | Discover | 0 to 1 | Watch three admins send a real message in the legacy product | Hesitations and workarounds recorded |
| DT-4 | Discover | 0 to 1 | Evidence pull: support tickets and Pendo data for the current Communications product; legacy usage of two-way, keywords, merge fields, translation and scheduling; P0-12 overlap numbers | One summary; D14 and D15 answered |
| DT-5 | Define | 1 | One-page problem brief: the three problems we commit to, a measure for each, and what v1 will not solve | Signed off by product; contradictions with this plan raised as changes to it |

**Diamond 2: the right solution.** Clickable prototypes, each tested in two
rounds with five admins. A prototype that fails twice is redesigned or cut, not
built.

| ID | Stage | Sprint | Ticket | Feeds |
|---|---|---|---|---|
| DT-6 | Develop | 2 to 3 | Compose: audience, reach, cost and "arrives by" shown before send | P4-3, P1-14 |
| DT-7 | Develop | 2 to 3 | Undo window and typed confirmation for large sends | P4-4 |
| DT-8 | Develop | 4 to 5 | Inbox with assignment, and the "which church is this for?" prompt. Test the prompt with **members** as well as admins: they are the ones who receive it | P5-2, P5-3, D12 |
| DT-9 | Develop | 4 to 5 | Opt-out and consent in plain words in the people list | P4-9, D10 |
| DT-10 | Develop | 6 to 7 | Queue honesty: what the admin sees when the queue is hours deep on a Sunday; reports and queued time | P4-7, P1-14 |
| DT-11 | Deliver | 8 to 9 | Usability test of the built widget with five admins against fakesendex | First message sent without help in under 10 minutes |
| DT-12 | Deliver | 9 | Instrument the pilot measures in section 9 | Each measure visible per church before the pilot starts |

### Phase 0, Sprint 0: foundations

| ID | Ticket | Done when |
|---|---|---|
| P0-1 | Answer D1 to D13 or accept defaults in writing (D14 and D15 follow DT-4 in Sprint 1). Send the D1/D2 questions to Esendex on day one | Decision log committed here |
| P0-2 | Rotate leaked HGS credentials, coordinated with the HGS team | Old keys rejected; live sender unaffected |
| P0-3 | Dev, staging, prod (D4), instance sizes recorded | `readyz` green in all three |
| P0-4 | CI/CD: build, test, deploy to staging on merge. CI runs **all** test modules | Merged PR reaches staging; `test_auth` runs |
| P0-5 | Migration runner; `schema.sql` becomes `0001_init.sql` | CI applies to empty and previous-version DBs |
| P0-6 | Secrets from the platform secret store | No secret in image or repo |
| P0-7 | Auth fixes A1, A2: enforce expiry; org id from signed/decrypted content; `UNITY_ALLOW_UNSIGNED` refused outside dev (D8) | Expired, replayed and org-swapped tokens all return 401 |
| P0-8 | Embed fixes E2, E3: refuse `*`; widget validates `event.origin`; drop `apiBase` from `init` | Tests for each |
| P0-9 | Capture a real two-recipient Esendex response; fix the parser and fixture (S19) | Parser test uses the real response |
| P0-10 | `tools/fakesendex` v1: accept, respond, fire postbacks | Passes the envelope tests |
| P0-11 | Test harness: Postgres and Redis integration tests for API and worker in CI | One end-to-end send test against fakesendex |
| P0-12 | Measure the overlap in legacy data, **per sender code**: handsets texted by more than one church on the same code within 7, 14 and 30 days, and the share of inbound replies they produce. Also: how often the inbound `ReferenceID` is present and names the church rule 4 would pick | Numbers recorded against D12 |
| P0-13 | **Sender inventory** from the legacy database: rows of `sms_long_codes` and `sms_providers`; church customers per code and provider (`customers.sms_long_code`, `sms_provider_id`, product default); staff users with their own number (`user_sms_preference`); active failovers (`saved_sms_*` columns) | One table in this repo; D1 and D13 answered from it |

### Phase 1, Sprints 1 to 3: engine correctness

Sprint 1: nothing lost, nothing doubled, STOP works.

| ID | Ticket | Done when |
|---|---|---|
| P1-1 | Delivery status state machine and claim columns (5.2) | Migration plus forward-only transition tests |
| P1-2 | Send worker claims rows, sends, writes results in one statement; broadcast counters (S1, S9, S10) | Redelivered batch sends nothing; midnight test passes |
| P1-3 | Unknown-outcome resolution, per-row retry, 30 s timeout, circuit breaker (S2, S3, S16) | fakesendex "accepted then timed out" yields zero duplicates and zero lost |
| P1-4 | Stream hygiene: reclaim of stuck messages, dead-letter, unique consumer names, trimming, `noeviction` (S4, S12, S13) | Killed worker's batch completes; Redis memory flat over 1M messages |
| P1-5 | Outbox dispatcher replaces API-side enqueue; honours `scheduled_at`; cancel; atomic idempotency (S5 to S8) | Redis down during create still sends later; concurrent retries return the same broadcast |
| P1-6 | Paged, resumable fan-out (S11, S6) | 1M recipients under 512 MB; crash mid-fan-out resumes without gaps or repeats |
| P1-7 | **Inbound processor:** STOP/START/HELP with the full word list, opt-out write at sender and church scope (D11), confirmation reply, near-miss flag (I1, I2) | STOP during a running broadcast halts further messages to that handset within 60 s |
| P1-8 | Webhook hardening (I3): secret, IP allow-list, known-sender check, size limit, no phone numbers in logs | Forged START is rejected |

Sprint 2: postbacks and the shared code.

| ID | Ticket | Done when |
|---|---|---|
| P1-9 | Status writer: batched updates, partition date from `ReferenceID`, forward-only, parking for early postbacks, `BackupMessageID` mapped to the original row, legacy receipt codes (000 and 003 delivered, 004 en route, others failed with reason) (I1, I4, I5) | 5,000 postbacks/s; out-of-order test passes; a receipt under a backup id updates the original row |
| P1-10 | `sender` table; churches reference a sender | Shared code is a row, not a default |
| P1-11 | Global rate limiter per sender; batch size derived from rate (S14, D2) | fakesendex never sees the limit exceeded |
| P1-12 | Scheduler in front of the send queue: weighted fairness across churches, three priority lanes (S14). **Budget the whole sprint for one engineer** | T4 fairness passes at small scale; reply overtakes a running broadcast |
| P1-13 | Send-time recheck of opt-out, consent and quiet hours per batch; pause and resume (S15) | Broadcast crossing 9pm pauses and resumes next morning |
| P1-14 | Delivery estimate from queue depth and rate, exposed by API | Within 20% in a staged test |

Sprint 3: completeness.

| ID | Ticket | Done when |
|---|---|---|
| P1-15 | Reconciler: expired leases re-queued; rows `accepted` with no receipt after 72 h become `failed (no receipt)` | No non-terminal row older than 72 h |
| P1-16 | Partition creation as a scheduled job; archiver to object storage per D9 (S17) | Runs daily; day-91 partition archived |
| P1-17 | Segment calculator: true GSM-7 alphabet, UCS-2, concatenation, church-name prefix (S18) | Table tests: emoji, é, £, 160/161, 70/71 |
| P1-18 | Consent gate (D10) | No-consent numbers excluded and counted in the preview |
| P1-19 | **Legacy opt-out import and two-way sync** with the HGS system. Source is the single `sms_optout` table, written by all providers' handlers. Rules: a row with code 0 is imported against **every** sender; a row with a known code is imported against that sender **and** any sender its churches have since been moved to (failover, the 22383 to 34166 move); a row whose code is not a known sender was inferred by legacy, so import it against every sender that handset's churches use. Over-suppressing is the safe error; START undoes it. Legacy START also clears `contacts.no_text_message` for that phone in every church; mirror that | Counts reconcile; a STOP on either side blocks both within 1 min |
| P1-20 | `usage_daily` rollup (segments and messages per church) | Matches `delivery` exactly |
| P1-21 | Metrics and alerts: queue depth per lane, send rate, failure rate, postback lag, dead letters, Redis memory | Alert fires in a staged failure |
| P1-22 | Connection budget: pooler, per-process pool sizes | Documented; holds at 20 replicas |

### Phase 2, Sprints 4 to 5: load proof

| ID | Ticket | Done when |
|---|---|---|
| P2-1 | fakesendex v2: all knobs in 7.1, per-sender rate enforcement, duplicate detection | |
| P2-2 | Seed generator: 5,000 churches, 12M people, direct to cache tables | Seeds staging in under 30 min |
| P2-3 | k6 scenarios T1 to T7 and a results report | One command per scenario |
| P2-4 | Run T2 to T7; fix what breaks (Sprint 5) | All pass |
| P2-5 | Run T1 24-hour soak (Sprint 5) | Passes; report in `docs/load/` |
| P2-6 | Nightly 1/100-scale run in CI | Build fails on lost or duplicate |
| P2-7 | Live canary (7.2) | Real rate recorded against D1; go or no-go on sender capacity |

### Phase 3, Sprints 6 to 7: service layer

Sprint 6: identity, API, metering.

| ID | Ticket | Done when |
|---|---|---|
| P3-1 | `partner`, API keys, scopes, per-key rate limits | Rotation with no downtime |
| P3-2 | `POST /v1/embed/sessions`; JWT carries allowed origins; Unity becomes one issuer | Widget works with either |
| P3-3 | Widget served at `/embed/widget?partner=`; per-partner `frame-ancestors` | Wrong origin refused |
| P3-4 | Roles: admin, sender, inbox-only, read-only | Role test per endpoint |
| P3-5 | Versioned OpenAPI spec; breaking changes fail CI | |
| P3-6 | Multi-campus: parent sees and sends to children | Tenant-isolation suite passes |
| P3-7 | `audit_log` | |
| P3-8 | Metering API and per-church limits (daily ceiling, suspend) from `usage_daily`. Invoicing stays in MB's billing system | Usage export matches delivery counts |

Sprint 7: directory and webhooks.

| ID | Ticket | Done when |
|---|---|---|
| P3-9 | Directory contract (5.4) published, with a mock | |
| P3-10 | Sync worker: incremental, cursors, backoff, deletes, full reconciliation | 200k-person church syncs in under 5 min |
| P3-11 | Amplify/Unity adapter (depends on D3) | Real org's groups appear |
| P3-12 | Inbound directory webhooks | Phone change reflected in under 10 s |
| P3-13 | Send-time refresh with 3 s budget and recorded staleness; phone de-duplication | Shared-phone household gets one text |
| P3-14 | Outbound webhooks: per-broadcast default, per-recipient opt-in, signed, retried | 1-hour partner outage loses no events |
| P3-15 | Erasure: partner delete request removes cached personal data | |
| P3-16 | Rerun T1 and T2 through the directory mock with webhooks on | Gates still pass |
| P3-17 | Partner onboarding guide: HTML, React, .NET | Outside developer embeds in under 30 min |

### Phase 4, Sprints 8 to 9: widget and embed SDK

Sprint 8.

| ID | Ticket | Done when |
|---|---|---|
| P4-1 | Apply D5: Amplify components, or split `widget.html` into modules | No view file over ~400 lines |
| P4-2 | Amplify tokens in one place; light and dark | Design sign-off |
| P4-3 | Compose: audience from partner groups; reach after opt-outs and no-consent; segments including church-name prefix; cost; **estimated delivery window**; schedule; test-send | Send disabled until all are shown |
| P4-4 | Undo window (60 s) and typed confirmation above 1,000 recipients | |
| P4-5 | MMS image attach, **only if D2 confirms Esendex supports it**; otherwise link to hosted image | |
| P4-6 | Embed SDK: own-origin default, correct path, token refresh, auto-resize, events, `views`, deep links, no microphone (E1, E4) | Section 5.5 snippet works unmodified on a blank page |

Sprint 9.

| ID | Ticket | Done when |
|---|---|---|
| P4-7 | Sent list, broadcast detail, per-recipient status, queued time, CSV export | Numbers match the database |
| P4-8 | Dashboard from `usage_daily` and broadcast counters | Under 1 s for a 200k-member church |
| P4-9 | People and groups (read-only) with consent and opt-out status | |
| P4-10 | Settings: quiet hours, staff roles, sender shown | |
| P4-11 | Accessibility | axe clean in CI; manual screen-reader pass |
| P4-12 | Playwright suite inside a host page, against fakesendex | Compose-to-delivered path in CI |
| P4-13 | Performance budget | Interactive under 2 s on a mid-range phone |

### Phase 5, Sprints 10 to 11: two-way, migration, pilot readiness

Sprint 10.

| ID | Ticket | Done when |
|---|---|---|
| P5-1 | `message` table, thread view, reply on the priority lane | Reply reaches handset in under 10 s during a broadcast |
| P5-2 | Inbound attribution rules 1 to 6 (5.3.1), hold-and-ask prompt, platform triage queue | Table test per rule; two-church handset is never attributed without an answer; replies to a dedicated number thread regardless of which staff member sent |
| P5-3 | Assign, close, reopen, notes, unread counts, near-miss opt-out flag | |
| P5-4 | Live updates by fetch streaming | New reply visible in under 5 s |
| P5-5 | Keywords and text-to-join: unique per sender, consent captured, added to partner group | Join visible in the partner product |
| P5-6 | Auto-replies, after-hours reply, reply notifications | |

Sprint 11.

| ID | Ticket | Done when |
|---|---|---|
| P5-7 | **Legacy migration:** churches, Unity org mappings, licence keys, senders with their licence keys (from P0-13), dedicated numbers (owned by the church, not a staff user; legacy has both user-level and church-level numbers), keywords (legacy looks up by keyword and code and takes the first row, with a null code meaning the default code, so report and resolve collisions before import). Only churches in scope under D13. Do **not** migrate shared-code inbound history into inboxes: the legacy rows carry no reliable church | Dry run reconciles on a production copy |
| P5-8 | Inbound cutover (D7): forward, compare for 7 days, switch. Legacy attribution is known to be wrong in places, so the bar is not "identical" | Every STOP/START/HELP difference is one of the known differences listed in 5.7, and none leaves a person opted in whom legacy opted out without an inbox flag; every attribution difference explained by a 5.3.1 rule |
| P5-9 | Church onboarding flow: create church, link partner org, assign sender, import consent | A new church sends in under 10 minutes |
| P5-10 | Internal support console: find a message, view a church's queue, suspend a church, cancel a running broadcast | Support can do each without a developer |
| P5-11 | Abuse controls: daily ceiling, link screening, alert on anomalous volume | |
| P5-12 | Spanish opt-out keywords (ALTO, PARAR) and a decision on UI languages | |
| P5-13 | **Pilot:** 5 to 10 churches, 4 weeks | Exit criteria in section 9 |

### Phase 6, Sprints 12 to 13: email

One engineer is reserved for pilot issues throughout.

| ID | Ticket | Done when |
|---|---|---|
| P6-1 | Extract the channel interface now that there are two | No Esendex import outside its module |
| P6-2 | Email adapter (D6), bounces, complaints, suppression | Hard bounce suppresses future sends |
| P6-3 | Per-church DKIM and SPF; shared fallback domain that rewrites `From` so DMARC aligns | Unverified domains send from the fallback and pass DMARC |
| P6-4 | Email compose using an existing editor component | Renders in Gmail, Outlook, Apple Mail |
| P6-5 | One-click unsubscribe header and link; preference page | |
| P6-6 | Opens, clicks, reports, complaint-rate alert at 0.1% | |
| P6-7 | Domain and IP warm-up schedule | Documented ramp; enforced by limiter |
| P6-8 | Combined send with per-person channel preference | |
| P6-9 | Rerun T1 and T2 with a 50/50 mix | Gate passes |

### Phase 7, Sprints 14 to 15: engagement

| ID | Ticket | Done when |
|---|---|---|
| P7-1 | Merge fields with fallback; per-recipient segment count | Missing name never sends "Hi ," |
| P7-2 | Templates, per church and shared by parent | |
| P7-3 | Short links on our domain, click tracking | Redirect p99 under 50 ms |
| P7-4 | Recurring broadcasts | |
| P7-5 | Automations v1 (all of Sprint 15): trigger (keyword, joined group, date) to a timed linear sequence. No branching builder | Guest follow-up sequence runs end to end |

### Phase 8, Sprint 16: launch

| ID | Ticket | Done when |
|---|---|---|
| P8-1 | External security review and penetration test (book in Sprint 10) | No open high or critical |
| P8-2 | Backup, point-in-time restore, failover drill | Restore under 1 h, rehearsed |
| P8-3 | Final load gate T1 to T7 on production-sized environment | Report published |
| P8-4 | Runbooks: provider outage, deep queue, stuck broadcast, cancel a bad send | On-call follows each cold |
| P8-5 | Status page, SLOs, on-call | |
| P8-6 | Partner docs site | |
| P8-7 | GA | |

---

## 9. Definition of done and gates

Every ticket: tests, CI green, forward-safe migration, metrics on anything in the
send path, docs updated, second-person review.

| Gate | Criteria |
|---|---|
| End of Phase 0 | D1, D10, D11, D13 answered; sender inventory (P0-13) done; auth and embed fixes merged |
| End of Phase 2 | Section 7.1 passes on SMS; live canary done; sender capacity plan agreed |
| Pilot start | Legacy opt-outs imported and syncing; consent gate on; support console live |
| Pilot exit | 4 weeks; zero duplicate or lost sends; delivery rate within 1 point of legacy; zero opt-out violations; pilot churches would not go back. Admin measures (DT-12): a new admin sends a first message without help in under 10 minutes; "arrives by" estimate within 20% of actual; share of replies answered within a working day is higher in week 4 than week 1; support contacts per church below legacy; cancelled or regretted sends tracked and falling |
| GA | Final load gate; security review closed; DR drill done; runbooks rehearsed |

Objectives at GA: API availability 99.9%; 99% of messages claimed within 60 s of
the sender's rate limit allowing them; inbox reply visible within 5 s.

---

## 10. Risks

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| One shared short code cannot carry 10M/day | **Near certain at ~100 msg/s** | High | D1 in Sprint 0; fairness, lanes and estimates built regardless; `sender` table makes adding capacity a data change |
| Carriers or Esendex restrict the shared-code model | Unknown | Very high | Written confirmation in D1; per-church numbers already supported |
| Partner numbers arrive with no consent evidence | High | High | D10; consent field in the contract; gate in P1-18 |
| Opt-outs lost between legacy and new system | Medium | Very high | P1-19 before any real send; two-way sync until cutover |
| Double-send on unknown provider outcome | Medium | High | Design 5.2; T5 gates every release |
| A private reply shown to the wrong church on the shared code | Happens today in legacy | High | 5.3.1: no silent guess; hold-and-ask for two-church handsets; D12 sized by P0-12 |
| Church customers turn out to be spread over several codes and providers, not one shared code | Likely, from the legacy code | Medium | P0-13 in Sprint 0; D13; `sender` is a table, so several codes are data |
| A church is moved to another sender and its opt-outs do not follow | Happens today in legacy failover | Very high | Opt-outs copied with the move (5.3); P1-19 import covers past moves |
| Esendex answers (D2) arrive late or are "no" | Medium | Medium | Design assumes "no" |
| Partner directory APIs slow or incomplete; Amplify may have none | Medium | High | D3 in Sprint 0; cache and reconciliation |
| The widget is built to an untested idea of what church admins need | High without the design track | High | DT-1 to DT-12; prototypes tested before Phase 4 builds them |
| Pilot churches lose legacy features they use (merge fields, translation) | Unknown until DT-4 | Medium | D14, D15 |
| Scope creep toward giving, forms, voice | High | Medium | Non-goals in 1.1 |

**Not yet verified, flagged so nobody builds on them:** which sender codes and
providers church customers are on today (code read only, no data: P0-13); the
stored procedure `sp_Alerts_GetCDyneTextAlert`, which resolves the legacy sender
and is in none of the repos read; how Esendex picks the `ReferenceID` on an
inbound reply and when it sends `BackupMessageID`; the short code's
contracted rate and carrier standing; Esendex MMS support, `ReferenceID`
de-duplication, in-request pacing and code-level STOP blocking; whether Amplify
exposes people and groups by API; the effective date of the FCC opt-out rule
(sources disagree between already in force and January 2027, so build to it now);
the legal retention period in D9.

---

## 11. Sources

Gathered 2026-11-28 from third-party pages; verify before external use.

- Flocknote: [Capterra](https://www.capterra.com/p/149433/Flocknote/), [Capterra pricing](https://www.capterra.com/p/149433/Flocknote/pricing/), [G2 pricing](https://www.g2.com/products/flocknote/pricing), [PricingSaaS](https://pricingsaas.com/companies/flocknote), [Software Advice](https://www.softwareadvice.com/church/flocknote-profile)
- Clearstream: [official pricing](https://clearstream.io/pricing), [Capterra](https://www.capterra.com/p/158103/Clearstream/), [Capterra reviews](https://www.capterra.com/p/158103/Clearstream/reviews/), [G2 pricing](https://www.g2.com/products/clearstream/pricing), [GetApp](https://www.getapp.com/nonprofit-software/a/clearstream/), [Krowdbase](https://www.krowdbase.com/software/clearstream)
- Text In Church and Gloo: [Text In Church reviews](https://capterra.com/p/142758/Text-In-Church/reviews/), [vs Clearstream](https://www.capterra.com/compare/142758-158103/Text-In-Church-vs-Clearstream), [Gloo reviews](https://www.capterra.com/p/230260/Gloo/reviews/)
- Carrier limits: [Telnyx 10DLC rate limits](https://developers.telnyx.com/docs/messaging/10dlc/10dlc-rate-limits), [Infobip 10DLC throughput](https://www.infobip.com/docs/usa-sender-registration/10dlc/10dlc-throughput-reference), [Twilio on T-Mobile daily limits](https://support.twilio.com/hc/en-us/articles/1260804800549-T-Mobile-daily-message-limits-for-long-code-messaging-with-A2P-10DLC), [Bird throughput and daily caps](https://docs.bird.com/applications/enable-channels/sms/sms-10dlc/troughput-and-daily-caps)
- Shared short codes: [Bandwidth on T-Mobile](https://www.bandwidth.com/blog/3-things-you-should-know-about-t-mobiles-ban-on-shared-short-codes/), [Infobip on AT&T](https://www.infobip.com/blog/the-att-shared-short-code-ban-next-steps-for-the-a2p-industry/)
- FCC opt-out rules: [Nelson Mullins](https://www.nelsonmullins.com/insights/alerts/fcc-download/all/fcc-adopts-overhaul-of-tcpa-consent-revocation-rules-revoke-all-narrowed-exclusive-opt-out-methods-permitted-and-fraud-alert-exemption-expanded), [Burr](https://www.burr.com/telephone-consumer-protection-act/the-fcc-delays-effective-date-of-tcpa-revoke-all-rule-until-january-31-2027)
