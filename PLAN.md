# Church Communications Platform: Build Plan

**Status:** revision 8, 2026-11-28. Final architecture, stack and phases, now
led by the product: the features that make a church choose this over
Clearstream, Text In Church, Flocknote and Gloo (next section).
**Audience:** the engineers building it and the people who must answer the
questions in section 12.

**Direction (product owner, 2026-11-28): build from scratch.** A new,
product-ready platform with its own database, packaged in Docker, delivered in
phases, integrated with Amplify and with AI assistance built in. This replaces
revision 6, which modernised the legacy stack in place
(`docs/archive/PLAN-rev6-modernise-in-place.md`). The direction has changed
twice in one day, so the decision is recorded here and should be confirmed once
with the people in Q1 before Phase 1 starts.

**What carries over.** Everything learned from reading the legacy source and
from two rounds of adversarial review still applies and is built in: the send
path rules that survived a three-architect debate (`docs/architecture-debate/`),
the opt-out and reply-attribution fixes, and the resized load gate.

**Two assumptions this plan makes, because the instruction was brief:**

1. **"Amplify AI"** is read as: the product lives inside Amplify, and ships with
   AI assistance (section 4.12). If it instead means a specific platform or
   service, tell us which (Q2).
2. **Stack:** the Python, Postgres and Docker code already in this repo is the
   starting point, since it matches "docker, database". If the team that will
   own this is a .NET team, the same design ports; decide before Phase 1 (Q3).

**Limits.** Based on reading source code. Nothing was run, no production data
was seen. Every volume, latency and sprint figure is an estimate.

---

## Product: why a church chooses this

Competitor facts below come from a web search on 2026-11-28 (sources in section
13). "Not found" means the search did not show it, not that it does not exist.
Check vendor pages before quoting any of this outside the team.

### Where the competition stands

| | Clearstream | Text In Church | Gloo | Flocknote |
|---|---|---|---|---|
| Strength | Mass text, workflows, a Planning Center add-on; 2026 added QR codes, scheduling from the inbox, a voice add-on | Guest follow-up: prebuilt workflows, digital connect cards, keywords; a voicemail assistant with transcription | Free tier; an AI assistant churches can train on their sermons | Email and text by member count; signups and giving tracking |
| AI in messaging | Not found | Not found; automation is rule-based | Assistant and chat for leaders; AI replies through a partner | Not found |
| What reviewers complain about | Members cannot reply to the group; cannot resend a failed message from the phone | Price; slow sync with Planning Center | Confusing message counter and vocabulary | Little found |

Wider signals: churches' use of AI rose from 25% to 45% in a year, mostly for
writing communications; 64% of leaders say a church needs an AI policy and 5%
have one; and churches are finding that more messages do not mean more
connection.

### Ten features we lead with

| # | Feature | What the admin gets | Why it beats them | Release |
|---|---|---|---|---|
| F1 | **Always-current people, and audiences you describe** | "Parents of kids in Sunday school who have not signed up for camp" becomes a list they can see and confirm. No import, no sync | Reads Amplify's people directly, so the sync delay reviewers complain about cannot happen. Plain-language audiences were not found elsewhere | Alpha |
| F2 | **Honest delivery** | Reach, cost and "arrives by" shown before send; a send-by time; nothing arrives late without them knowing | No credit maths, the top complaint category. No competitor was found showing a delivery estimate | Alpha |
| F3 | **Write it well, fast** | Draft from a brief, fit to one text, fix tone, catch the wrong date before it goes out | AI in the compose box was not found in any of the four | Alpha |
| F4 | **Care inbox** | Every reply reaches a person. Prayer requests and urgent messages are flagged, assigned, and tracked until answered. A reply to a small-group message goes to that group's leader | Answers the "members cannot reply to the group" complaint, and treats replies as care, not tickets | Pilot |
| F5 | **Nobody falls through** | Journeys for guests, new members and people who have gone quiet. Steps are drafted by AI and approved by a person. Entry by connect card, QR code or keyword | Matches Text In Church's best feature and adds "gone quiet", which needs the people data only we have | 1.0 |
| F6 | **Less is more** | A warning when people are being over-texted; a page where each member picks topics, channel, language and how often | Acts on what churches are learning about fatigue. Not found elsewhere | 1.0 |
| F7 | **Every language in the room** | The message offered in each recipient's language, approved per language before it sends | Legacy already translates; no competitor was found doing it | 1.0 |
| F8 | **Emergency in two taps** | Weather closure or a safety alert jumps the queue on every channel | Built on the priority lane, and on legacy's school-alert heritage | Pilot |
| F9 | **AI a church can govern** | A church sets its own AI rules: off, suggest only, which features, who may use them. Every use is logged | 64% of leaders want an AI policy and 5% have one. We ship the policy with the product | Alpha |
| F10 | **The right channel** | One send reaches each person by text, rich message (RCS) or email, with fallback. RCS shows the church's own name and logo as a verified sender | Rich messaging also eases the shared-number identity problem. Depends on the provider (Q19) | 1.x |

### Matching what they already have

Keywords and text-to-join; QR codes for keywords; digital connect cards;
scheduling; templates; merge fields; polls and RSVPs by reply with live
results; short links with click tracking; staff use from a phone, including
resending a failed message; multi-campus.

### Later, not in 1.0

Voicemail and call forwarding with transcription; group conversations where
members reply to each other; an assistant members can ask questions; WhatsApp.

**The cost of this.** These features add about three sprints to revision 7 (20
instead of 17 to general availability). F1 to F3 and F9 are in the alpha so the
product is distinctive the first time a church sees it.

---

## 1. Goals, constraints, definition of product-ready

### 1.1 Goals

| # | Goal | Measured by |
|---|---|---|
| G1 | A message never waits because of our software | Targets in 1.4 |
| G2 | Scales out by adding containers, and by adding senders as data | Load gate, section 9 |
| G3 | Nobody who opted out gets a text; no duplicate blasts; no silent losses | Standing criteria, section 9 |
| G4 | A church admin can compose, send, see results and answer replies without help | Design track measures, section 7 |
| G5 | Lives inside Amplify: one login, Amplify's people, Amplify's look | Phase 3 exit |
| G6 | AI makes the admin faster and never sends, opts in, or decides compliance by itself | Guardrails, 4.12 |
| G7 | Churches move from legacy without losing an opt-out or a Sunday | Migration design, section 5 |

**Non-goals for the first release:** voice calls, giving, forms and event
signups, a native mobile app, a contact database of our own. Email is Phase 7.

### 1.2 Constraints

- **Shared short codes.** Many churches send from the same number. Legacy shows
  several codes by brand (22383, 34166, 29988, 31263, 39256), some dedicated
  numbers, and provider paths for Esendex (main), Nexmo, Sinch and Twilio. A
  short code carries on the order of 100 segments a second; ours is unknown.
- **Volume.** Millions of messages a month with a Sunday-morning peak. Design
  and gate for ten times the measured peak day, not a hundred times.
- **Legacy keeps running** for every church not yet moved, on the same codes.
- **Team size is unknown** (Q4). Planning numbers assume three engineers and a
  part-time designer.

### 1.3 Product-ready means

Each phase ends in something a real user can use. "Product-ready" at general
availability means all of:

- multi-tenant with tenant-isolation tests on every endpoint;
- single sign-on from Amplify, roles, audit log;
- compose, schedule, send, reports, inbox, keywords, settings;
- opt-out, consent and quiet-hours rules enforced at send time;
- church onboarding in under 10 minutes; a support console; usage metering;
- dashboards, alerts, runbooks, on-call, a status page;
- security review and penetration test closed; backup restore rehearsed;
- accessibility to WCAG 2.1 AA.

### 1.4 Targets

| Measure | Target |
|---|---|
| "Send" pressed to first message handed to the provider | under 5 s, any size |
| 500-person broadcast, last message handed over, idle code | about 6 s |
| Staff reply or emergency during a mass send | handed over within 5 s |
| A STOP takes effect | before the next provider call |
| Time our pipeline adds to a 100,000-person broadcast | under 1 minute |
| Inbox reply visible to staff | within 5 s |

**What no architecture changes.** At 100 segments a second, 100,000 messages
take about 17 minutes on one code and a 400,000-message Sunday takes over an
hour. If that is not acceptable the answer is sender capacity (4.9).

---

## 2. What the legacy system teaches

Verified in the legacy source. These are the failures the new platform must not
repeat, and the behaviours churches rely on.

| Legacy behaviour | Evidence | In the new platform |
|---|---|---|
| Polls every 10 s; one thread builds the audience and sends inline | `App.config` `check_interval`; `CallQueue.vb:2352` | Queued stages, scale-out workers |
| About six database round trips per recipient | `CallQueue.vb:2534-2553`; `PrimeMessageSender.vb:423-513` | Set-based SQL |
| Duplicate blast: a result-write error resends the batch after 30 s | `PrimeMessageSender.vb:341-345` | Never resend on a guess (4.5) |
| Silent loss: a failed provider call is only emailed | `PrimeMessageSender.vb:327-330` | Every outcome is a row |
| Opt-out checked once; parse failure counts as "not opted out" | `CallQueue.vb:2534`, `3316-3329` | Checked at submit; fails closed |
| No rate limit; 2,000 recipients dumped per call | `cdyne_sms_batch_size` | One scheduler, five seconds in flight |
| Receipt handler sleeps 60 s on an early receipt | `cdyne_sms_response.aspx.vb:226-230` | Parked and retried |
| Replies filed under whichever church last sent a two-way text | `utilities.vb:753-804` | Attribution rules (4.7) |
| Opt-outs keyed by handset and code, no church | `sms_optout` model | Same scope, plus church-wide rows |
| Sender resolved per staff user, customer, product, default; operators can move churches between numbers | `PrimeMessageSender.vb:63-79`; `SmsFailoverController.cs` | `sender` table; opt-outs follow the church |
| Merge fields, per-recipient translation, name prefix on every message | `PrimeMessageSender.vb:98-161` | Merge fields Phase 3; translation through AI assist |
| A partner iframe embed and an Amplify people feed already exist | `ParishSoftController.cs:52-177`; `HGS.Amplify.Sync.Function` | Reuse the feed; replace the embed deliberately (Q1) |

---

## 3. Final tech stack

| Concern | Choice | Why |
|---|---|---|
| Packaging | **Docker** images; `docker compose` for local and CI; one image, several commands (api, worker roles) | Asked for; already in the repo |
| Cloud runtime | **Azure Container Apps** | Ministry Brands runs on Azure; managed, no cluster to operate (confirm in Q5) |
| Database | **PostgreSQL 16**, managed (Azure Database for PostgreSQL Flexible Server), zone-redundant | Own database, as asked. It is the system of record **and** the queue of record |
| Backend | **Python 3.12, FastAPI, asyncpg**, Pydantic | Already here, with a live-verified Esendex envelope and working Unity SSO |
| Queues | **Tables in Postgres** claimed with `FOR UPDATE SKIP LOCKED` and a lease; `LISTEN/NOTIFY` to wake workers | One transaction can suppress opt-outs, mark rows submitted and record the attempt. A broker cannot |
| Cache or broker | **None.** Redis is removed | The debate found nothing it is needed for; fewer parts to run |
| Migrations | **Alembic** (or plain numbered SQL run in CI) | Forward-safe, applied to empty and previous-version databases in CI |
| Frontend | **React and TypeScript (Vite)**, Amplify components and tokens if they exist (Q6) | Replaces the 2,700-line `widget.html`; matches the React used in `HGS-PWA` |
| Sign-on | **Amplify Unity SSO**, exchanged for a short-lived session token | Already implemented; needs the expiry and signing fixes |
| People and groups | **Amplify feed** (the existing Kafka topic or an API), cached in Postgres | No church imports contacts into this product |
| Provider | Esendex SOAP behind an `SmsProvider` interface | Other providers become further implementations |
| AI | An `Assistant` interface; **Amplify's AI service if one exists, otherwise Claude through the API** | Keeps the product independent of one vendor call path (Q2) |
| Object storage | Azure Blob Storage | Media, exports, archives |
| Observability | OpenTelemetry to Application Insights | Dashboards and alerts from day one |
| CI/CD | GitHub Actions today (Q7): build, test, migrate, deploy to staging on merge | Already has a CI workflow |
| Tests | pytest with real Postgres; Playwright for the UI; **k6** and a mock provider for load | |

---

## 4. Architecture

### 4.1 Principles

1. **A row is the unit of work.** Every queue is a table claimed with a lease.
   `NOTIFY` only wakes workers; losing one loses nothing.
2. **Decide before you send, in one transaction.** Opt-out suppression, the
   send-by and quiet-hours checks, the attempt record and the change to
   `submitted` commit together, before the provider is called.
3. **Never resend on a guess.** A timeout or a dead worker leaves recipients
   `unknown`: alerted on, not resent.
4. **Backlog waits on our side.** Only about five seconds of a sender's rate is
   inside the provider, so a STOP and a priority reply still take effect.
5. **Store before you acknowledge.** A provider postback is committed before the
   endpoint answers.
6. **Fail closed.** A number that will not parse, a missing budget, a stale
   feed: do not send.
7. **AI assists; rules decide.** No AI output sends a message, lifts an opt-out
   or grants consent.

### 4.2 Shape

```
 Amplify shell                      Esendex / other providers
 ┌──────────────────┐                     ▲            │ postbacks
 │ React app (embed)│                     │            ▼
 └────────┬─────────┘               SmsProvider   Postback endpoint
          │ HTTPS, session token          ▲       (store row, then 200)
          ▼                               │            │
     ┌─────────┐                      Senders          │
     │   API   │                         ▲             │
     └────┬────┘                         │             ▼
          ▼                              │
 ┌──────────────────────────── PostgreSQL 16 ─────────────────────────────┐
 │ church, sender, person cache, consent, optout, broadcast, delivery,    │
 │ send_batch, delivery_attempt, postback, message, keyword, usage, audit │
 └───▲──────────▲───────────▲────────────▲──────────────▲────────────▲────┘
     │          │           │            │              │            │
  Fan-out   Scheduler   Reconciler   Postback       Directory     Assistant
  (N)       (1 leader)  (1)          processor (N)  sync          (AI calls)
```

All boxes below the API are roles of the same Docker image.

### 4.3 Components

| Role | Claims from | Does | Scales |
|---|---|---|---|
| **API** | | Creates broadcasts (insert only), serves the app, mints sessions | N |
| **Dispatcher** | due broadcasts | Starts broadcasts at their scheduled time; atomic idempotency | 1 leader |
| **Fan-out** | started broadcasts | Expands the audience set-based, in pages with a cursor, into `delivery` | N |
| **Scheduler** | queued `delivery` | The only spender of a sender's budget. Forms `send_batch` rows by lane and church | 1 leader under an advisory lock, with heartbeat and takeover |
| **Sender** | `send_batch` | Runs the submit transaction, calls the provider, records the result | N |
| **Reconciler** | batches `submitted` past a deadline | Resolves from postbacks to accepted or `unknown`; re-queues expired `claimed` leases only | 1 |
| **Postback endpoint** | provider calls | Inserts into `postback`, answers 200 | N |
| **Postback processor** | `postback`, in order per handset | Forward-only statuses; STOP, START, HELP; keywords; attribution | N, partitioned by handset hash |
| **Directory sync** | Amplify feed | Keeps the people and group cache current | 1 per source |
| **Assistant** | API requests | Calls the AI service with guardrails (4.12) | N |

### 4.4 The submit transaction

For a claimed batch, one transaction:

1. Mark rows `suppressed` where the handset has an opt-out on any sender this
   church has ever used, a church-wide opt-out, or no consent.
2. Mark rows `expired` past the broadcast's send-by time; `held` in quiet hours.
3. Insert the `delivery_attempt`: attempt number,
   `ReferenceID = {send_date}-{batch}-{attempt}`.
4. Set the survivors to `submitted`.

Then the provider call. The result write is fenced on batch and attempt.

### 4.5 Duplicates and losses

| Event | Outcome | What makes it safe |
|---|---|---|
| Broadcast created twice | Same broadcast returned | Unique idempotency key, insert-or-return |
| Fan-out re-run after a crash | Nothing inserted twice | Unique `(send_date, broadcast_id, phone)`; table partitioned on `send_date` |
| Batch claimed by two senders | Cannot happen | `SKIP LOCKED` |
| Lease expires during a slow call | No resend | The call happens only after `submitted`; leases cover only `claimed` |
| Worker dies after the provider accepted, or the call times out | Rows stay `submitted`; reconciler resolves from postbacks, else `unknown` | Never resent on elapsed time |
| Provider rejects explicitly | New attempt | Unique `(batch, attempt)` |
| Short provider response | Remainder stays `submitted`, not failed | Reconciler |
| Postback twice, late or out of order | No effect | Keyed on provider message id; forward-only |
| Postback before the send result | Parked and retried | `postback` row with a retry time |
| A retried message reported under a new id | Mapped to the original | `BackupMessageID` handling |

Which fields Esendex postbacks carry is not known (Q9). If receipts lack our
`ReferenceID`, more crash cases end `unknown`; none are duplicated.

### 4.6 Opt-outs, consent, quiet hours

- **Scope.** Keyed by sender and handset, taken from the number the person
  texted. A STOP to a church's own number also writes a church-wide row.
  Opt-outs follow a church to every sender it has ever used.
- **Checked at submit** and stored before acknowledged.
- **Matching.** STOP: stop, stopall, stop all, quit, end, revoke, opt out,
  optout, cancel, unsubscribe, unsub, block, arret, on the whole message; a
  short message with one of these as a whole word ("please stop") also counts.
  Anything else that looks like one is flagged and **sends to that handset are
  held until a person reviews it**. START: start, resume, unstop; never "yes".
  HELP: help, hlp, aide, info. Spanish keywords in Phase 4.
- **START is narrow.** It removes keyword opt-outs on the number texted. It
  never removes an opt-out recorded by a church's staff.
- **Consent.** Each number carries a consent status, source and date. Legacy
  holds no consent evidence, so what counts as consent for migrated numbers is
  a legal ruling, due before Phase 5 (Q10). The gate is built in Phase 4.
- **Quiet hours** by recipient timezone; a send that reaches them pauses and
  resumes. The emergency override is explicit and audit-logged.

### 4.7 Inbound replies and attribution

Applied in order; church and sender are written on the inbound row on arrival.

1. STOP, START, HELP: apply to the number that was texted.
2. Dedicated number: the church that owns it, whoever sent the message.
3. Keyword: the church that owns that keyword on that code. Keywords are unique
   per sender.
4. Shared code, exactly one church texted this handset in the last 14 days:
   that church.
5. More than one church in the window: hold the message and ask once, "Which
   church is this for? Reply 1 for A, 2 for B", using public display names. Never
   for a message that is or looks like an opt-out.
6. No church in the window: a triage queue and a generic reply.

While legacy still sends on the same code, rules 4 and 5 must also count legacy
sends (5.3), and exactly one system answers each inbound.

### 4.8 Rate, fairness, priority

- **One spender.** Budget per sender = contracted segments per second minus
  segments in flight, in flight capped at about five seconds of rate.
- **Lanes, strict:** emergency, then one-to-one replies, then broadcasts, with
  no reserved share.
- **Fairness:** deficit round-robin across churches within a lane.
- **Leader safety:** heartbeat; a leader that holds the lock without progress
  is replaced within seconds and an alert fires.

### 4.9 When demand exceeds the carrier rate

Queued rows wait in `delivery`. Compose shows an estimated finish time. A
broadcast past its send-by time pauses and tells the church. Capacity is a row
in `sender` (number, provider, licence key, segments per second, daily cap);
more of it comes from a higher contracted rate, more codes, or the largest
churches on their own registered numbers (Q8, Q11).

### 4.10 People, groups and Amplify

- People and groups belong to Amplify. We keep a cache so an inbound text can
  be matched instantly and a partner outage does not stop a Sunday send.
- Source: the Amplify people feed that `HGS-PWA` already consumes from Kafka,
  or an Amplify API. Whether the feed carries mobile numbers and group
  membership is not visible in the code (Q12).
- Two people may share a phone. Audience expansion de-duplicates on phone.
- The recipient snapshot in `delivery` is the record of what was sent.

### 4.11 App, embed and sign-on

- One React app, shown inside Amplify. It can also be embedded by iframe with a
  short-lived session token that carries the allowed parent origins.
- Unity SSO tokens are exchanged for that session token. Fixes required before
  any real use: enforce expiry; take the organisation id only from signed or
  decrypted content; refuse unsigned tokens outside development; refuse `*` as
  an allowed origin; validate `event.origin`.
- Roles: admin, sender, inbox-only, read-only. Multi-campus: a parent sees and
  sends to its campuses.

### 4.12 AI assistance

Assumption until Q2 is answered: AI features inside this product, reached
through one `Assistant` interface.

| Feature | What it does | Phase |
|---|---|---|
| Compose assist | Draft from a short brief; shorten to fit a segment count; change tone; fix grammar | 3 |
| Pre-send check | Flags likely mistakes (a date that does not match its weekday, no place or time, a broken link) and content carriers reject. Advisory only | 3 |
| Translation | Offers the message in a recipient's language; the admin approves each language before it is used | 6 |
| Reply assist | Suggests a reply for staff to edit and send | 6 |
| Triage | Marks an inbound as urgent or pastoral care; marks opt-out-like wording, which **holds** sends to that handset. It can add a hold; only the keyword rules or a person can opt someone in or out | 6 |
| Insights | Plain-language summary of a broadcast; suggested send time from the church's own history | 6 |

**Guardrails, all required before any AI feature ships:**

- A person approves every outgoing message. Nothing is sent by AI.
- Prompts contain one church's data only; tenant isolation is tested.
- Phone numbers are never sent to the model. Names only where the feature
  needs them.
- Inbound texts are untrusted input: they are passed as data, and nothing in
  them can change what the assistant is allowed to do.
- Every AI call is recorded in the audit log with who asked and what was used.
- A church can turn AI off. Usage is metered per church.
- An evaluation set (real, anonymised church messages) must pass before each
  feature is released and on every model change.

### 4.13 Data model

| Table | Holds |
|---|---|
| `partner`, `church`, `campus` | Tenants, timezone, quiet hours, status, AI on or off |
| `sender` | Number, type, provider, licence key, segments per second, daily cap, shared or dedicated |
| `person`, `grp`, `grp_member` | Cache of Amplify people and groups, with external ids |
| `consent`, `optout` | Per handset: status, source, date; opt-out scope (sender or church-wide) and source |
| `broadcast` | Message, audience, schedule, send-by, lane, counters |
| `delivery` | One row per recipient, partitioned by `send_date`, unique per broadcast and phone |
| `send_batch`, `delivery_attempt` | One row per provider call and per attempt |
| `postback` | Raw provider postbacks, append-only |
| `message`, `conversation` | Inbox messages both ways, assignment, state |
| `keyword` | Unique per sender |
| `usage_daily` | Segments and messages per church per day |
| `audit_log`, `ai_call` | Who did what; every AI request |

---

## 5. Living beside legacy

Building from scratch brings back the risks the reviews found in running two
systems on the same codes. This section is the design for that period.

### 5.1 Opt-outs: import everything, over-suppress on doubt

Four sources, all imported before the first real send:

1. `sms_optout`: a code-0 row goes against every sender; any other row against
   its code.
2. `sms_inbound` rows that were STOPs: import against `to_number`, the number
   actually texted, because the code legacy recorded may have been inferred.
3. `contacts.no_text_message`: a church-wide opt-out.
4. Re-run the new matcher over the stored inbound history and import what
   legacy missed ("stop all" on Esendex; "quit", "end", "unsub" elsewhere).

Past sender moves cannot be reconstructed (reversing a failover erases the
saved number), so each opt-out is also imported against every code that handset
was ever sent from. Legacy START behaviour is **not** copied.

### 5.2 Two-way sync during the parallel run

A STOP in either system blocks both within a minute. The legacy failover tool
is frozen for churches that have moved.

### 5.3 Shared codes during the parallel run

- Legacy sends on a code unpaced, so our scheduler's fairness on that code is
  partial until the whole code has moved. Move one whole code at a time,
  smallest first.
- Reply attribution must count legacy sends on the same code.
- Exactly one system answers any inbound message.
- Where Esendex delivers inbound replies decides whether inbound can move
  church by church or only code by code (Q9).

### 5.4 Rollback

A church can be moved back until its code is fully migrated. Opt-outs and
replies taken here flow back to legacy. The rollback is rehearsed before the
pilot.

---

## 6. Phases

Two-week sprints. Each phase ends in something usable. Planning numbers for
three engineers and a part-time designer; they are not commitments.

| Phase | Sprints | Ends with |
|---|---|---|
| 0 Foundations | 0 | Environments, pipeline, security fixes, real provider captures, legacy baselines |
| 1 Engine | 1 to 3 | Real texts to test numbers through the full send path, correct under failure |
| 2 Proof | 4 | Load gate passed |
| 3 Product MVP | 5 to 8 | A church admin describes an audience, writes with AI help, sees reach, cost and arrival time, sends and reads results inside Amplify (F1, F2, F3, F9). **Internal alpha** |
| 4 Two-way, care and compliance | 9 to 11 | Care inbox, emergency send, keywords, polls, consent gate, quiet hours (F4, F8) |
| 5 Migration and pilot | 12 to 13, then 4 weeks | 5 to 10 real churches moved. **Pilot** |
| 6 Journeys and engagement | 14 to 16 | Follow-up journeys, fatigue guard, member preferences, translation, insights (F5, F6, F7) |
| 7 Email and channels | 17 to 18 | Email, channel choice per person, RCS trial (F10) |
| 8 Launch | 19 | Security review, restore drill, final load gate. **General availability (1.0)** |

### Phase 0, Sprint 0

| ID | Ticket | Done when |
|---|---|---|
| P0-1 | Confirm direction and ownership (Q1), stack (Q3), cloud (Q5) | Written here |
| P0-2 | Docker image with roles; compose for local; staging and production on Container Apps; managed Postgres | Health check green in all three |
| P0-3 | Pipeline: build, test, migrate, deploy to staging on merge; all test modules run | A merged change reaches staging |
| P0-4 | Migration runner; current `schema.sql` becomes migration 1 | CI applies to empty and previous-version databases |
| P0-5 | Secrets from the platform secret store; add `Legacy code/` to `.gitignore`; rotate provider keys committed in legacy repos | No secret in image or repo |
| P0-6 | Auth and embed fixes (4.11) | Expired, replayed and organisation-swapped tokens return 401 |
| P0-7 | Capture real Esendex responses and the postbacks that follow | Fixtures in the repo; Q9 answered |
| P0-8 | Legacy baselines: peak day and hour, messages per code, time to last accepted, duplicate and never-finalised rates; sender inventory | One table in this repo |
| P0-9 | Remove Redis; workers wake on `LISTEN/NOTIFY` | Compose has no Redis |
| P0-10 | Test harness with real Postgres; one end-to-end send against the mock provider | In CI |

### Phase 1, Sprints 1 to 3: engine

| ID | Ticket | Done when |
|---|---|---|
| P1-1 | Schema: `delivery` partitioned by `send_date` with the unique index; `send_batch`, `delivery_attempt`, `postback`, `sender` | A second row for the same broadcast and phone is rejected, across midnight too |
| P1-2 | Dispatcher: insert-only API, scheduled start, cancel, atomic idempotency | Concurrent retries return the same broadcast |
| P1-3 | Fan-out: set-based, paged, resumable | 1,000,000 recipients under 512 MB; crash resumes with no gap or repeat |
| P1-4 | Sender and the submit transaction (4.4); 30 s timeout; circuit breaker | A test for every row in 4.5 |
| P1-5 | Scheduler: budget in segments, lanes, deficit round-robin, heartbeat and takeover | A reply overtakes a running broadcast; a killed leader is replaced in under 10 s |
| P1-6 | Reconciler | No row left `claimed` or `submitted` past its deadline without an alert |
| P1-7 | Postback endpoint and processor: store then 200, secret, known-sender check, forward-only, parking, `BackupMessageID` | A STOP survives an instance kill; out-of-order test passes |
| P1-8 | STOP, START, HELP with the matcher in 4.6; hold on near-miss; confirmation texts per sender | "Yes" does not re-subscribe; START does not lift a staff opt-out |
| P1-9 | Segment calculator: true GSM-7, UCS-2, concatenation, name prefix | Table tests: emoji, é, £, 160/161, 70/71 |
| P1-10 | Partition creation and archive as scheduled jobs | Day-91 partition archived |
| P1-11 | Dashboards and alerts: oldest queued row per lane, budget used, `unknown` count, leader heartbeat, postback lag | Alert fires in a staged failure |
| P1-12 | Mock provider built from the P0-7 captures, with correlated fault knobs | Passes the envelope tests |

### Phase 2, Sprint 4: proof

| ID | Ticket | Done when |
|---|---|---|
| P2-1 | Seed generator and k6 scenarios | One command per scenario |
| P2-2 | Run the load gate (section 9) with 89 days of history loaded | All pass; report in `docs/load/` |
| P2-3 | Nightly small-scale run in CI | Build fails on a lost or duplicate message |
| P2-4 | Live canary with Esendex's agreement: 1,000 to 10,000 real messages to owned numbers | Real rate and receipt timing recorded |

### Phase 3, Sprints 5 to 8: product MVP

| ID | Ticket | Done when |
|---|---|---|
| P3-1 | React app shell, Amplify look, sign-on, roles | Design sign-off |
| P3-2 | Directory sync from Amplify; cache; phone de-duplication | A real organisation's groups appear |
| P3-3 | Compose: audience, reach after opt-outs, segments, cost, estimated finish, schedule, send-by, test send | Send is disabled until all are shown |
| P3-4 | Undo window and typed confirmation for large sends | |
| P3-5 | Merge fields with fallback | A missing name never sends "Hi ," |
| P3-6 | Sent list, broadcast detail, per-recipient status, export | Numbers match the database |
| P3-7 | Settings: quiet hours, staff roles, sender shown | |
| P3-8 | `Assistant` interface, guardrails and evaluation set (4.12) | Tenant-isolation and no-phone-number tests pass |
| P3-9 | Compose assist and pre-send check | Passes the evaluation set; admin can ignore every suggestion |
| P3-10 | Audit log; usage metering | Usage matches delivery counts |
| P3-11 | Accessibility and a Playwright suite inside a host page | Automated checks clean in CI |
| P3-13 | **Described audiences (F1):** plain-language request becomes a saved filter over Amplify people; the admin sees the people and the filter before using it | The filter, not the AI, selects people; same request gives the same list; never crosses churches |
| P3-14 | **Church AI policy (F9):** per-church settings (off, suggest only, features, roles); a plain-language policy page a church can adopt | A feature that is off cannot be called; every call is in the audit log |
| P3-15 | Templates; staff use from a phone-width screen, including resending a failed message | |
| P3-12 | **Internal alpha:** one internal or friendly church sends real messages | Feedback recorded against the design track |

### Phase 4, Sprints 9 to 11: two-way, care and compliance

| ID | Ticket | Done when |
|---|---|---|
| P4-1 | Inbox: threads, assign, close, notes, unread counts, live updates | Reply visible in 5 s; reply sent on the priority lane |
| P4-2 | Attribution rules 1 to 6, the "which church?" prompt, triage queue with an access policy | A table test per rule; two-church handset never attributed silently |
| P4-3 | Keywords and text-to-join, unique per sender, consent captured | Join visible in Amplify |
| P4-4 | Consent gate and send-time quiet hours | No-consent numbers excluded and counted in the preview |
| P4-5 | Spanish keywords; auto-replies; reply notifications | |
| P4-6 | Abuse controls: daily ceiling per church, link screening, one-click suspend | |
| P4-7 | **Care inbox (F4):** flags for prayer request and urgent (AI suggests, a person confirms); assignment to a care team; time-to-answer tracking; reply assist | No flagged message is unassigned after a working day without an alert |
| P4-8 | **Reply to the leader (F4):** a message sent for a small group routes replies to that group's leader | Leader sees the reply in the app and by notification |
| P4-9 | **Emergency send (F8):** a two-step flow on the emergency lane; restricted by role; audit-logged | Starts sending within 10 s during a mass send |
| P4-10 | Polls and RSVPs by reply with live results; QR codes for keywords; digital connect card | Results match replies |

### Phase 5, Sprints 12 to 13, then a four-week pilot

| ID | Ticket | Done when |
|---|---|---|
| P5-1 | Opt-out import from four sources (5.1) and two-way sync (5.2) | Counts reconcile; a STOP on either side blocks both in a minute |
| P5-2 | Migration: churches, organisation mapping, senders with licence keys, keywords with collisions resolved | Dry run reconciles on a production copy |
| P5-3 | Church onboarding flow; dedicated-number registration where needed | A new church sends in under 10 minutes |
| P5-4 | Support console: find a message, view a queue, suspend a church, cancel a send | Support can do each without a developer |
| P5-5 | Inbound cutover per code; one system answers; rollback rehearsed | Every difference from legacy is a rule in 4.6 or 4.7 |
| P5-6 | **Pilot:** 5 to 10 churches | Exit criteria in section 9 |

### Phases 6 to 8

| ID | Ticket |
|---|---|
| P6-1 | **Journeys (F5):** trigger (connect card, keyword, joined a group, a date, gone quiet) to a timed sequence; steps drafted by AI and approved once by a person; no branching builder in 1.0 |
| P6-2 | **Gone quiet (F5):** a list of people who have stopped engaging, from Amplify data (Q18), offered to the care team; never messaged automatically |
| P6-3 | **Fatigue guard (F6):** warn at compose when recipients have had many messages this week; a per-church weekly cap with an emergency exception |
| P6-4 | **Member preferences (F6):** a page where a member chooses topics, channel, language and frequency; honoured at submit |
| P6-5 | **Translation (F7)** with per-language approval |
| P6-6 | Insights: broadcast summary, suggested send time; numbers that never deliver reported back for clean-up |
| P6-7 | Recurring broadcasts; short links with click tracking |
| P7-1 | Email channel: provider, bounces, complaints, per-church sending domains, one-click unsubscribe |
| P7-2 | **Channel choice (F10):** one send, each person on their preferred channel, with fallback |
| P7-3 | **RCS trial (F10)** with one church, if the provider supports it (Q19): verified sender name and logo, read receipts, suggested replies, fallback to text |
| P8-1 | External security review and penetration test (book in Phase 5) |
| P8-2 | Backup, point-in-time restore and failover drill |
| P8-3 | Final load gate on production-sized environment; runbooks; status page; on-call |
| P8-4 | General availability |

---

## 7. Design track: the church admin's side

Runs from Sprint 0 and feeds Phase 3 and 4. Nobody has been interviewed yet;
these are hypotheses to test: will it arrive on time, did I pick the right
people, who replied and was it answered, am I safe from texting someone who
opted out, what will it cost, and will the AI help or get in my way.

| ID | Stage | Sprint | Ticket | Done when |
|---|---|---|---|---|
| DT-1 | Discover | 0 | Recruit 8 to 10 admins, including two large and two multi-campus churches | Sessions booked |
| DT-2 | Discover | 0 to 1 | Interviews and watching three admins send in the current product | Notes for each |
| DT-3 | Discover | 0 to 1 | Evidence: support tickets, Pendo data, legacy feature usage (two-way, keywords, merge fields, translation, email, voice, credits) | One summary |
| DT-4 | Define | 1 | One-page brief: three problems we commit to, a measure for each | Signed off |
| DT-5 | Develop | 2 to 4 | Prototypes: compose with reach, cost and finish time; undo; AI compose assist | Each tested twice with five admins |
| DT-6 | Develop | 5 to 7 | Prototypes: inbox, the "which church?" prompt (tested with members too), opt-out and held status | Each tested twice |
| DT-7 | Deliver | 8, 11 | Usability test of the built product |
| DT-8 | Discover | 0 to 1 | Rank F1 to F10 with the recruited admins: which would make them switch, which they would not use | Order of the feature list confirmed or changed | First message sent without help in under 10 minutes |

---

## 8. Compliance notes

- Carriers have moved against shared short codes. Whether ours have a written
  exception is unknown and could force churches onto registered numbers (Q11).
- STOP, START and HELP reply texts must name the program and a contact and need
  carrier approval per sender.
- The "which church?" prompt and the triage queue show message content to
  people outside the church; both need an access policy and legal review (Q13).
- AI: churches must be told what is sent to an AI service, and a data
  processing agreement must cover it (Q2, Q13).

---

## 9. Load gate and exit criteria

**Load gate** (end of Phase 2, repeated before general availability), against
the mock provider with 89 days of history loaded.

| Scenario | Load | Pass |
|---|---|---|
| T1 Peak day | Ten times the measured legacy peak day over 24 h, per-code rates as contracted | Standing criteria; database CPU under 60% |
| T2 Sunday shape with a postback storm | Half the day's volume in two hours on one code, 2,000 postbacks a second | No church waits more than twice its fair share; replies handed over in 5 s; status lag under 30 s |
| T3 Whale | One 500,000-person broadcast beside normal traffic | Others slowed by no more than their fair share |
| T4 Chaos | Kill senders mid-call, kill the scheduler leader, fail over Postgres, provider brownout with late postbacks | Standing criteria after recovery |
| T5 STOP mid-broadcast | | Nothing sent to a handset more than 10 s after its STOP is stored |
| T6 Midnight and a daylight-saving change under load | | Standing criteria |

Standing criteria, judged against the seeded audience: every expected recipient
ends delivered, failed, rejected, suppressed, expired or `unknown`; `unknown`
only in the dead-worker and timeout cases and under 1 in 100,000 (a planning
figure, Q14); the mock received nobody twice; no sender exceeded its rate;
nothing sent to an opted-out or no-consent number.

**Pilot exit:** four weeks; zero duplicate blasts; zero opt-out violations;
delivery rate within one point of legacy; first message handed over in under
5 s; a new admin sends without help in under 10 minutes; share of replies
answered within a working day rises from week 1 to week 4; pilot churches would
not go back.

---

## 10. Risks

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| A finished platform that churches cannot or will not move to | Medium | Very high | Alpha in Phase 3 with a real church; consent ruling and feature usage before Phase 5; design track |
| Two products for the same customers (`HGS-PWA` already has an embed and is being worked on) | Unknown | High | Q1 before Phase 1 |
| A person who opted out in legacy is texted here | High without 5.1 | Very high | Four-source import; over-suppress on doubt; sync both ways |
| The carrier rate keeps messages waiting | Near certain on busy Sundays | High | Honest estimates and send-by; capacity as data; Q8 |
| Carriers do not accept the shared short codes | Unknown | Very high | Dedicated numbers are rows in `sender`; Q11 |
| A private reply reaches the wrong church during the parallel run | Medium | High | 5.3; no silent guess |
| AI writes something wrong and it is sent | Medium | High | A person approves every message; pre-send check; evaluation set |
| AI exposes one church's data to another, or sends phone numbers to a model | Low with guardrails | Very high | Isolation tests; numbers never in prompts; audit of every call |
| Esendex postbacks lack the fields the reconciler needs | Unknown | Medium | More `unknown`, never duplicates; P0-7 first |
| Schedule: about 20 sprints is a lot for a small team | High | Medium | Each phase is usable alone; stop after Phase 5 and treat 6 to 8 as a second project |
| The feature list is our guess at what beats competitors; no church has ranked it | High | High | DT-8 in Sprint 0; cut what admins would not use |
| Features that depend on Amplify data (gone quiet, described audiences) find the data is not there | Unknown | High | Q18 before Phase 3; F1 degrades to groups and tags |
| "Gone quiet" or care flags feel like surveillance to members | Medium | High | Offered to the care team only; never automatic messages; told to members in the preference page; legal review |
| RCS is not available through our provider or on shared numbers | Unknown | Low | F10 is 1.x; text and email do not depend on it |
| Direction changes again | It has, twice | High | Confirm Q1 once, in writing, before Phase 1 |

---

## 11. What is verified and what is not

**Verified in source:** every row in section 2; the current repo's stack and
defects listed in earlier reviews (postbacks durable only in Redis, `delivery`
partitioned on creation time, "yes" as a START word, tokens without expiry).

**Carried from the reviews and the debate, reasoned not measured:** that
Postgres as the queue is sound at this volume; the write-churn concern, which
the load gate must measure with history loaded; all latency figures.

**Assumed:** the meaning of "Amplify AI"; Azure as the cloud; Python as the
stack; a team of three.

---

## 12. Open questions

Ordered by how much each could change the plan.

| # | Question | Why it matters | Who |
|---|---|---|---|
| Q1 | Is building from scratch agreed by whoever owns `HGS-PWA` and the legacy system, and does this product replace the PWA's messaging and embed or sit beside it? | Decides whether two teams build the same thing | Product and engineering leads |
| Q2 | What exactly is "Amplify AI": AI features inside this product (assumed), an existing Ministry Brands AI service we must call, or something else? Which AI provider is approved, and under what data agreement? | Decides section 4.12 and the `Assistant` implementation | Product; Amplify team; legal |
| Q3 | Which team will own this, and is Python acceptable to them, or should it be .NET to match `HGS-PWA`? | The design is the same; the code and hiring are not | Engineering leads |
| Q4 | How many engineers, and is there a designer? | Sets the calendar; one person cannot deliver this on these dates | Product |
| Q5 | Is Azure the target cloud, and is Container Apps with managed Postgres approved? | Phase 0 environments | Platform team |
| Q6 | Does Amplify have a component library and design tokens, and how does a product get mounted in the Amplify shell? | Phase 3 frontend | Amplify design and platform |
| Q7 | Where does the code live long term? It is on a personal GitHub organisation today; the legacy repos are in Azure DevOps | Access, pipelines, secrets | Engineering leads |
| Q8 | What rate is contracted on each short code, and what would more capacity cost? | The real ceiling on waiting | Esendex account rep |
| Q9 | Esendex behaviour: which fields postbacks carry, one receipt per message or per segment, how late "accepted" can arrive, where inbound replies are delivered, whether one call can carry many messages, whether it de-duplicates on `ReferenceID` | Reconciler, inbound cutover, personalised sends | Esendex support; P0-7 |
| Q10 | What counts as consent for numbers migrated from legacy, which holds no consent evidence? | Decides whether a migrated church can text anyone on day one | Legal |
| Q11 | Are the shared short codes approved by the carriers in writing? | Could force registered numbers per church | Esendex; legal |
| Q12 | Does the Amplify people feed carry mobile numbers, group membership and deletions? | Decides Phase 3 directory work | Amplify team |
| Q13 | May platform staff read replies in triage; may the prompt name churches to a handset; what must churches be told about AI use? | Needed before the pilot | Legal; product |
| Q14 | What rate of `unknown` outcomes is acceptable, and who is told? | Sets the load-gate ceiling | Product |
| Q15 | Who grants read access to the legacy production database for the baselines and the opt-out import? | Blocks P0-8 and P5-1 | HGS team |
| Q16 | Do churches on Nexmo, Sinch and Twilio keep those providers? | Size of the provider work | Product; HGS team |
| Q17 | Which legacy features must exist before a church can move: email, voice, credits, imports, translation? | Sets migration scope church by church | DT-3; product |
| Q18 | What does Amplify hold and expose per person: groups, attendance, serving, events, children, giving? | F1 and F5 are only as good as this data | Amplify team |
| Q19 | Does Esendex, or another provider we may use, support RCS business messaging, and can a church be a verified sender while texts still come from a shared code? | Decides F10 | Esendex; product |
| Q20 | How will this be priced: by member count, by message, or included in Amplify? Can we promise "no credits"? | F2's claim depends on it; competitors are attacked on credit confusion | Product; finance |
| Q21 | Should members be able to reply to a whole group, or only to its leader? | Group conversations by text are costly and can expose numbers; the plan routes to the leader | Product; DT-8 |

---

## 13. Sources

Searched 2026-11-28. Third-party pages; verify before external use.

- Clearstream: [changelog](https://clearstream.io/changelog?page=1), [homepage](https://clearstream.io/), [Capterra reviews](https://www.capterra.com/p/158103/Clearstream/reviews/), [Planning Center add-on](https://www.planningcenter.com/blog/clearstream-texting-now-in-planning-center)
- Text In Church: [homepage](https://textinchurch.com/), [automated workflows](https://textinchurch.com/how-it-works/messaging/automated-workflows), [pricing](https://textinchurch.com/talk/pricing), [Capterra reviews](https://capterra.com/p/142758/Text-In-Church/reviews/), [Planning Center add-on](https://www.planningcenter.com/blog/2025/03/text-in-church-messaging-now-in-planning-center)
- Gloo: [Capterra reviews](https://www.capterra.com/p/230260/Gloo/reviews/), [GetApp listing](https://www.getapp.com/marketing-software/a/gloo-1/), [Faith Assistant press release](https://www.gloo.com/press/releases/gloo-and-faith-assistant-partner-to-build-custom-ai-for-churches-and-ministries), [Workspace](https://gloo.com/workspace), [VisitorReach](https://www.visitorreach.com/blog/ai-designed-for-churches), [what church communication is teaching us in 2026](https://www.gloo.com/resources/blog/what-church-communication-is-teaching-us-in-2026)
- Flocknote: [pricing change tracker](https://pricingsaas.com/companies/flocknote/diffs/2026W26), [review directory](https://subscribed.fyi/flocknote/reviews/)
- PastorsLine: [Capterra](https://www.capterra.com/p/156080/PastorsLine/), [G2 reviews](https://www.g2.com/products/pastorsline/reviews)
- Trends: [Pushpay on AI for church communications](https://pushpay.com/?p=18825), [MinistryWatch on AI in church operations](https://ministrywatch.com/amp/ai-in-church-operations-shifts-from-early-adopter-to-mainstream/), [Infobip messaging trends](https://www.infobip.com/messaging-trends-report), [Steve Fogg on church communications in 2026](https://stevefogg.com/2026/04/21/church-communications-in-2026-whats-changed-and-what-matters/)
