# Church Messaging: Modernisation Plan

**Status:** revision 6, 2026-11-28. Final architecture and stack. **Audience:**
the engineers doing the work and the people who must answer the questions in
section 12.

**This is not a rewrite.** The legacy database, the product UI (`HGS-PWA`), the
reports and the data model stay. What changes is the SMS send path and the
handling of provider postbacks: they move from a polling Windows Service and a
Web Forms page to scale-out workers with durable queues.

**How this revision was decided.** Three architects were each assigned a
different design (Service Bus between every stage; the database as the queue; a
separate store and event stream), proposed it against the legacy code, then
attacked each other's proposals. The papers are in `docs/architecture-debate/`.
Section 5 records what was adopted from each and why. Revision 5, which planned
a new standalone engine, is kept at `docs/archive/PLAN-rev5-new-engine.md`.

**Limits of this plan.** It is based on reading the legacy source. Nothing was
run, no production data was seen, and the database tier, real volumes and the
provider contract are unknown. Those gaps are listed in section 12, not hidden.

---

## 1. Goals, constraints, targets

### 1.1 Goals

| # | Goal | Measured by |
|---|---|---|
| G1 | A message never waits because of **our** software | Targets in 1.3 |
| G2 | The send path scales out by adding instances, and by adding senders as data | Load gate, section 9 |
| G3 | Nobody who opted out gets a text; no duplicate blasts; no silent losses | Standing criteria, section 9 |
| G4 | Churches keep sending every week while this is done | Per-church switch with rollback, section 7 |
| G5 | A private reply is never filed under the wrong church without anyone noticing | Attribution rules, 4.7 |

**Non-goals:** replacing the database, the product UI, voice or email sending;
a new people directory; a new embed (the PWA already has one). The Python
prototype in `app/` is frozen and will not be deployed (section 5).

### 1.2 Constraints

- **Shared short codes.** Many churches send from the same number. Legacy code
  shows several codes by brand (22383, 34166, 29988, 31263, 39256), some
  dedicated numbers, and provider paths for Esendex (main), Nexmo, Sinch and
  Twilio. A short code carries on the order of 100 segments a second; ours is
  not known.
- **Volume.** Millions of messages a month with a Sunday-morning peak. Design
  for ten times the measured peak day; do not design for a hundred times.
- **No big-bang cutover.** Each church can be switched and switched back.
- **Small team.** Two to three engineers. Every new platform piece is a cost.

### 1.3 Targets

| Measure | Target | Today |
|---|---|---|
| "Send" pressed to first message handed to the provider | under 5 s, any size | up to 10 s before pickup, then a serial loop |
| 500-person broadcast, last message handed over, idle code | about 6 s | unmeasured |
| Staff reply or emergency during a mass send | handed over within 5 s | waits inside the provider's queue |
| A STOP takes effect | before the next provider call for that church | checked once, when the audience is built |
| Time added by our pipeline to a 100,000-person broadcast | under 1 minute | unmeasured |

**What no architecture changes.** At 100 segments a second, 100,000
single-segment messages take about 17 minutes on one code, and a 400,000-message
Sunday takes over an hour. If that wait is not acceptable, the answer is more
sender capacity (4.9), not more software.

---

## 2. Where messages wait and break today

All verified in the legacy source.

| Problem | Evidence |
|---|---|
| Polls for work every 10 s | `check_interval` = 10000, `MessagingService/App.config` |
| One thread builds the audience and sends inline, recipient by recipient | `CallQueue.vb:2352` onward |
| About six database round trips per recipient, in series | opt-out select `CallQueue.vb:2534`; inserts at 2548 and 2553; select and insert per result `PrimeMessageSender.vb:423-513` |
| Personalised messages go one provider call per person | batch size forced to 1, `CallQueue.vb:2383-2386` |
| **Duplicate blast:** a result-write error sleeps 30 s and resends the whole batch | `PrimeMessageSender.vb:341-345` |
| **Silent loss:** a failed provider call is caught and only emailed | `PrimeMessageSender.vb:327-330` |
| Opt-out checked once at audience build; a parse failure counts as "not opted out" | `CallQueue.vb:2534`, `3316-3329` |
| No rate limit: 2,000 recipients per call are dumped into the provider's queue, so a staff reply waits behind them | `cdyne_sms_batch_size` = 2000 |
| Stops taking work above 60 concurrent messages; a crash mid-send leaves it half sent with no resume | `MessagingServiceCore.vb:213`, `2582` |
| Receipt handler sleeps 60 s holding a web thread when a receipt beats its send record | `cdyne_sms_response.aspx.vb:226-230` |
| Replies are filed under whichever church last sent a two-way text to that handset, with no time limit | `utilities.vb:753-804` |
| The callback copy to Service Bus uses a random hash as the message id, so duplicate detection never matches | `AzureServicesClient.vb:118` |

---

## 3. Final tech stack

| Concern | Choice | Why this, not the alternative |
|---|---|---|
| Language and runtime | **C# on .NET 10** | Matches `HGS-PWA`, the actively developed product. All three architects chose it |
| System of record and queue of record | **Existing Azure SQL database**, new schema `msg` | One transaction can suppress opted-out numbers, mark rows submitted and write the legacy tables. A broker cannot do that; a second database cannot do that |
| Workers | **.NET Worker Service** (`BackgroundService`) on a dedicated **Azure App Service plan**, Always On, two or more instances | The team already deploys App Service by zip from Azure DevOps. Throughput is carrier-bound, so Functions autoscale and containers add nothing; no repo has a Dockerfile |
| Postback endpoint | **ASP.NET Core minimal API**, its own App Service app on the same plan | Isolated from the product API so a postback storm cannot take the UI down |
| Broker | **Azure Service Bus Standard**, already in use | Used only for postback processing (sessions give per-handset ordering, plus dead-lettering and scheduled retry) and wake-up pings. Every message is a pointer to a row, so losing one loses nothing |
| Data access | `Microsoft.Data.SqlClient` with Dapper; set-based SQL, table-valued parameters, `SqlBulkCopy` | The claim statements need exact SQL. Already a dependency of `HGS.Providers` |
| Provider client | Esendex SOAP through `System.ServiceModel.Http`, behind an `ISmsProvider` interface | Nexmo, Sinch and Twilio become further implementations when their codes migrate |
| Cache | **None** | Rate and fairness state lives in SQL; nothing to keep consistent |
| Observability | OpenTelemetry to **Application Insights** | Already used by the CoreApi Functions |
| CI/CD | **Azure DevOps** YAML pipelines, zip deploy with staging slots, idempotent SQL migration scripts as a pipeline step | Follows `HGS-PWA/Pipelines/azure-pipelines-release.yml` |
| Product UI | **`HGS-PWA`** (React, .NET 10 API), including its existing partner embed | Already built and in use |
| Load test | **k6** plus a mock provider (`fakesendex`, ported from the prototype's idea) | |

**Not adopted:** Kafka or Event Hubs, Postgres, Redis, Container Apps, Azure
Functions for the send path, and the Python prototype. Reasons in section 5.

---

## 4. Architecture

### 4.1 Principles

1. **A row is the unit of work; a message is only a nudge.** Every queue is a
   table in `msg` claimed with a lease. Service Bus messages point at rows.
2. **Decide before you send, in one transaction.** Opt-out suppression, the
   send-by check, the state change to `submitted` and the legacy-table write
   commit together, before the provider is called.
3. **Never resend on a guess.** A timeout or a dead worker leaves recipients
   `unknown`: alerted on, not resent. A missed text can be recovered by a
   person; a duplicate to thousands cannot.
4. **Backlog waits on our side.** Only about five seconds of the sender's rate
   is ever inside the provider, so a STOP and a priority reply still take effect.
5. **Fail closed.** A number that will not parse, a stale lease, a missing
   budget row: do not send.

### 4.2 Shape

```
 HGS-PWA (UI, embed)          Esendex / other providers
        │ alerts.status = Ready        ▲            │ postbacks
        ▼                              │            ▼
 ┌───────────────────────── Azure SQL (schoolcast_live) ─────────────────────┐
 │ legacy: alerts, call_queue, call_recipients, text_message_response,       │
 │         sms_optout, sms_inbound, contacts ...                              │
 │ msg:    broadcast_job → delivery → send_batch → delivery_attempt          │
 │         sender, sender_budget, postback, pilot_customer, control          │
 └───────▲──────────▲───────────▲────────────▲───────────────▲───────────────┘
         │          │           │            │               │
     Fan-out    Scheduler     Senders    Reconciler     Postback processor
     (N)        (1 leader)    (N)        (1)            (N, by session)
                                  │                          ▲
                                  └── ISmsProvider           │ pointer
                                                     Service Bus topic
                                                             ▲
                                                     Postback endpoint
                                              (stores the row, then answers 200)
```

### 4.3 Components

| Stage | Claims from | Does | Scales |
|---|---|---|---|
| **Fan-out** | `msg.broadcast_job`, created by reserving an alert with the legacy statement (`status` 1 to 2) | Runs the legacy audience query once, set-based; inserts `msg.delivery` in pages of 2,000 with a cursor, so a crash resumes | N instances |
| **Scheduler** | queued `msg.delivery` rows | The only component that spends a sender's budget. Forms `msg.send_batch` rows by lane and by church | One leader under `sp_getapplock`; standby takes over |
| **Sender** | `msg.send_batch` with `UPDLOCK, READPAST, ROWLOCK` | Runs the submit transaction (4.4), calls the provider, records the result | N instances |
| **Reconciler** | batches `submitted` past a deadline | Matches postbacks to attempts; resolves to accepted or `unknown`; re-queues expired `claimed` leases only | One |
| **Postback endpoint** | provider HTTP calls | Inserts the raw postback into `msg.postback`, publishes a pointer, answers 200 | N instances |
| **Postback processor** | Service Bus topic, session per message id (receipts) or per handset (inbound) | Forward-only status updates; STOP, START, HELP; keywords; reply attribution | N instances |
| **Sweeper** | rows with no progress | Re-publishes pointers for unprocessed postbacks after 30 s; raises alerts | One |

Fan-out, scheduler, sender, reconciler and sweeper are hosted services in one
deployable (`HGS.Messaging.Worker`). The endpoint and processor are a second
(`HGS.Messaging.Postback`).

### 4.4 The submit transaction

For a claimed batch, one transaction:

1. Mark rows `suppressed` where the handset has an opt-out on **any sender this
   church has ever used**, or a code-0 row, or the contact is flagged no-text.
2. Mark rows `expired` if the broadcast is past its send-by time, and `held` if
   the recipient is in quiet hours.
3. Insert the `delivery_attempt` row: attempt number and
   `ReferenceID = {batch}-{attempt}`.
4. Set the surviving rows to `submitted`.
5. Write the same recipients to `call_queue` and `call_recipients`, so legacy
   reports and legacy reply attribution see them immediately.

Then the provider call. The result write is fenced:
`WHERE batch = @b AND attempt = @a AND status = submitted`.

### 4.5 Duplicates and losses

| Event | Outcome | What makes it safe |
|---|---|---|
| Alert picked up twice | Second reserve updates nothing | Legacy reserve statement, `MessagingServiceCore.vb:2582` |
| Fan-out re-run after a crash | Inserts nothing twice | Unique `(alert_id, phone)` on `msg.delivery` |
| Batch claimed by two senders | Cannot happen | Row lock with `READPAST` |
| Lease expires during a slow call | No resend | The call happens only after `submitted`; leases cover only `claimed` |
| Worker dies after the provider accepted | Rows stay `submitted`; the reconciler resolves them from postbacks, else `unknown` | Never resent on elapsed time |
| Provider call times out | Same as above | Same |
| Provider rejects explicitly | Retried as a new attempt | Unique `(batch, attempt)` |
| Result write fails | Retried; no resend | Fenced on attempt |
| Postback delivered twice, late or out of order | No effect | Keyed on provider message id; statuses only move forward |
| Postback arrives before the send result | Rescheduled a few seconds out | Service Bus scheduled message replaces the 60 s sleep |
| Service Bus loses or duplicates a pointer | No effect | The row is the truth; the sweeper re-publishes |

The reconciler depends on which fields Esendex postbacks carry, which nobody has
captured (Q4). If receipts do not carry our `ReferenceID`, more recipients end
`unknown` after a crash; none are duplicated.

### 4.6 Opt-outs

- **One table.** Legacy `sms_optout` stays the only store, so there is no import,
  no second copy and no sync to get wrong.
- **Checked at submit**, not only at audience build (4.4). A STOP that arrives
  mid-broadcast stops every batch not yet submitted. Exposure is what the
  provider already holds: about five seconds of rate.
- **Stored before acknowledged.** The postback row is committed before the
  endpoint answers 200.
- **Matching.** STOP: stop, stopall, stop all, quit, end, revoke, opt out,
  optout, cancel, unsubscribe, unsub, block, arret, matched on the whole
  message; a short message containing one of these as a whole word ("please
  stop") is also an opt-out. Anything else that looks like one is flagged for a
  person and **sends to that handset are held until it is reviewed**. START:
  start, resume, unstop; never "yes". HELP: help, hlp, aide, info.
- **START is narrow.** It removes keyword opt-outs on the number that was
  texted. It does not clear a church's no-text flag. Legacy clears that flag for
  the phone in every church (`utilities.vb:808-823`); the new processor must not.
- **Backfill.** Run the new matcher once over the stored `sms_inbound` history
  and add the opt-outs legacy missed ("stop all" on Esendex; "quit", "end",
  "unsub" on the other providers' handlers).
- **Parse failures do not send.**

### 4.7 Inbound replies and attribution

Applied in order, with the church and sender written on the inbound row when it
arrives:

1. STOP, START, HELP: apply to the number that was texted.
2. Dedicated number: the church that owns it, whoever sent the message.
3. Keyword: the church that owns that keyword on that code.
4. Shared code, exactly one church texted this handset from the code in the
   last 14 days: that church.
5. Shared code, more than one church in the window: hold the message and ask
   once, "Which church is this for? Reply 1 for A, 2 for B", using public
   display names only. Never for a message that is or looks like an opt-out.
6. No church in the window: a triage queue, plus a generic reply naming the
   service and how to opt out.

Because the new path writes `call_queue` inside the submit transaction, legacy
and new sends are visible to the same rules throughout the migration.
Esendex's `MatchedMessageID` and the `ReferenceID` on an inbound are stored as
hints and measured before they are trusted to break a tie.

### 4.8 Rate, fairness, priority

- **One spender.** Only the scheduler assigns budget, so there is no distributed
  token bucket. Budget per sender = contracted segments per second minus
  segments in flight, with in flight capped at about five seconds of rate.
- **Lanes, strict:** emergency, then one-to-one replies, then broadcasts.
  No reserved share: an idle priority lane costs broadcasts nothing.
- **Fairness:** deficit round-robin across churches within a lane. A
  500,000-person broadcast gets its share, not the whole code.
- **Leader safety.** The scheduler writes a heartbeat. A leader that holds the
  lock but stops making progress is detected in seconds, alerted on, and its
  session is killed so the standby takes over.
- **During migration** legacy still dumps 2,000-recipient calls onto a shared
  code unpaced. Fairness on a code is best-effort until the **whole code** has
  moved, which is why migration goes one code at a time (section 7).

### 4.9 When demand exceeds the carrier rate

Queued rows wait in `msg.delivery`. The compose screen shows an estimated
finish time (segments ahead divided by rate). A broadcast that reaches its
send-by time pauses and tells the church; it does not arrive at 11.

Capacity is data: a row in `msg.sender` with its number, provider, licence key,
segments per second and daily cap. Three ways to add it, in order of effort:
a higher contracted rate on a code; more codes with churches spread across
them; the largest churches on their own registered numbers. Which of these is
open, and carrier rules on shared codes may force the third (Q2, Q3).

### 4.10 Personalised messages

Legacy sends merge-field messages one call per person. The Esendex request
takes an array of messages (`advReq.SMSRequests`, `PrimeMessageSender.vb:321`),
so one call may be able to carry many different texts. If it can (Q5), a
personalised broadcast is batched like any other; if not, senders make the
calls in parallel within the sender's budget.

### 4.11 Data model (`msg` schema, no foreign keys into legacy tables)

| Table | Holds | Key points |
|---|---|---|
| `broadcast_job` | One row per alert being expanded | `alert_id` primary key, lease, cursor |
| `delivery` | One row per recipient | Unique `(alert_id, phone)`; filtered index on queued rows `(sender_id, lane, customer_id, id)`; partitioned by send date so retention is a partition truncate, not a delete |
| `send_batch` | One row per provider call | Lease; filtered index on open batches |
| `delivery_attempt` | One row per attempt | Unique `reference_id`; provider message ids |
| `postback` | Raw provider postbacks, append-only | Written before the 200 |
| `sender`, `sender_budget` | Numbers, providers, licence keys, rates | Adding capacity is an insert |
| `pilot_customer` | Churches on the new path | Deleting the row is the rollback |
| `control` | Pause flag, mirrors legacy `comm_control` | |

---

## 5. What the debate settled

| Question | Decision | From |
|---|---|---|
| Where does send state live? | In the existing Azure SQL database, schema `msg` | B |
| Opt-out check, state change and legacy write | One transaction, before the provider call | B; conceded by A and C |
| Is a broker in the correctness path? | No. Service Bus carries pointers for postbacks and wake-ups | A's "pointer" principle, B's rule |
| Rate limiting | Budget = rate minus in flight, one spender, strict lanes, no reserved share | B and C; A's 20% reserve rejected because it slows every broadcast when no priority traffic exists |
| First migration step | Postbacks, in shadow, before any sending moves | A |
| Our own postback URL per request | Yes, so new-path postbacks never touch the legacy page | C |
| Shadow audience comparison as a release gate | Yes, off-peak and sampled | C, amended by B |
| Send-by deadline that pauses instead of delivering late | Yes | A and C |
| Per-handset ordering of inbound | Service Bus sessions, already used in production | A |
| Migration unit | Church by church, completing one whole code at a time | B |

**The separate store (C) was rejected, with a trigger to revisit.** Copying
results back to the legacy tables with a lag lets a quick reply be filed under
the wrong church, it needs two opt-out copies, and it adds two platforms for a
team of three. C's concern stands, though: nobody knows whether the shared
database has Sunday headroom (Q1). `msg` has no foreign keys into legacy tables
so it can move. **Trigger:** if the load gate or production shows send-path
load slowing the product, move `msg` to its own Azure SQL database, accepting
that the opt-out check then becomes a cross-database read.

**The Python prototype (`app/`, `db/`, `web/`) is frozen.** It has its own
church and people tables, so adopting it would be the rewrite that was ruled
out, and its send loop lacks the send-time opt-out check, rate limit and attempt
fencing. Its Esendex envelope tests and segment rules are ported to .NET.

---

## 6. Design track: the church admin's side

Runs in parallel from week one; none of it depends on the pipeline. Nobody has
been interviewed yet, so the five problems are hypotheses:

1. Will it get there on time?
2. Did I pick the right people?
3. Who replied, and has anyone answered?
4. Am I safe from texting someone who opted out?
5. What will it cost?

| ID | Stage | When | Ticket | Done when |
|---|---|---|---|---|
| DT-1 | Discover | Sprint 0 | Recruit 8 to 10 church admins, including two large and two multi-campus | Sessions booked |
| DT-2 | Discover | Sprint 0 to 1 | Interviews: what they send, when, what went wrong last time | Notes for every session |
| DT-3 | Discover | Sprint 0 to 1 | Watch three admins send a real message in the current product | Hesitations recorded |
| DT-4 | Discover | Sprint 0 to 1 | Evidence: support tickets, Pendo data, legacy feature usage | One summary |
| DT-5 | Define | Sprint 1 | One-page brief: the three problems we commit to and a measure for each | Signed off by product |
| DT-6 | Develop | Sprint 2 to 3 | Prototype: reach, cost and "arrives by" before send; send-by time | Tested twice with five admins |
| DT-7 | Develop | Sprint 2 to 3 | Prototype: undo window and confirmation for large sends | Tested twice |
| DT-8 | Develop | Sprint 4 to 5 | Prototype: inbox, assignment and the "which church?" prompt, tested with members too | Tested twice |
| DT-9 | Develop | Sprint 4 to 5 | Prototype: opt-out and held-for-review status in plain words | Tested twice |
| DT-10 | Deliver | Sprint 6 onward | Build the passing designs in `HGS-PWA` | Admin measures in section 9 |

---

## 7. Migration plan

Two-week sprints. Planning numbers for two to three engineers; expect slips at
steps that need another team or the provider.

### Sprint 0: measure, and stop the bleeding

| ID | Ticket | Done when |
|---|---|---|
| M0-1 | Read the database tier, Sunday CPU, log and lock figures; confirm an index on `sms_optout (number_dialed, short_code)` | Recorded here; Q1 answered |
| M0-2 | Production query: peak day and hour, messages per code, time from alert ready to last accepted, duplicate rows per alert and phone, sends with no final status | Baseline for every target in 1.3 |
| M0-3 | Sender inventory: `sms_long_codes`, `sms_providers`, churches per code and provider, active failovers | One table in this repo |
| M0-4 | Capture real Esendex responses and the postbacks that follow: fields, one receipt per message or per segment, timing, whether one call takes many messages | Fixtures in the repo; Q4, Q5 answered |
| M0-5 | **Legacy hotfix:** remove the 30 s whole-batch resend; record a failed provider call against the recipients instead of only emailing | Deployed; duplicate rate falls in M0-2's query |
| M0-6 | Rotate the provider keys committed in the HGS repos; add `Legacy code/` to `.gitignore` here | Old keys rejected |
| M0-7 | Read the body of `sp_Messaging_GetAlerts` and `sp_Alerts_GetCDyneTextAlert` from the database into source control | In the repo |
| M0-8 | New repo, solution skeleton, pipeline to a staging slot | A merged PR reaches staging |

### Sprint 1 to 2: postbacks first

| ID | Ticket | Done when | Rollback |
|---|---|---|---|
| M1-1 | Fix the legacy callback copy: stable message id, real session id; turn the existing switch on | Every Esendex callback also reaches the topic | Switch off |
| M1-2 | `msg.postback` and the postback processor in **shadow**: compute the status it would write and diff against `text_message_response` | Seven days with differences explained | Stop the consumer |
| M1-3 | Postback endpoint: store, then 200; secret in the URL; known-sender check; no phone numbers in logs | A STOP survives a Service Bus outage and an instance kill | n/a (not yet pointed at) |
| M1-4 | Processor becomes authoritative for **receipts**; scheduled retry replaces the 60 s sleep; `BackupMessageID` handled. Inbound is forwarded unchanged to the legacy page | Legacy receipt branch disabled by flag | Flag |

### Sprint 3 to 4: the send path, for test customers only

| ID | Ticket | Done when |
|---|---|---|
| M2-1 | `msg` schema, `pilot_customer` (empty), `sp_Messaging_GetAlerts` skips pilot customers | With the table empty, nothing changes |
| M2-2 | Fan-out: set-based, paged, resumable | 1,000,000 recipients without a table lock; crash resumes with no gap or repeat |
| M2-3 | Sender and the submit transaction (4.4); Esendex adapter; our own postback URL | Table tests for every row in 4.5 |
| M2-4 | Scheduler: budget, lanes, deficit round-robin, heartbeat and takeover | A reply overtakes a running broadcast; a killed leader is replaced in under 10 s |
| M2-5 | Reconciler and sweeper | No row left `claimed` or `submitted` past its deadline without an alert |
| M2-6 | Segment calculator: true GSM-7, UCS-2, concatenation, the name prefix | Table tests: emoji, é, £, 160/161, 70/71 |
| M2-7 | **Shadow fan-out:** for a sample of real alerts, off-peak, build the audience without sending and diff against `call_queue` | Differences explained; this is the gate for M3 |
| M2-8 | Dashboards and alerts: oldest queued row per lane, budget used, `unknown` count, dead letters, leader heartbeat | Alert fires in a staged failure |
| M2-9 | Internal test customers send on a dedicated number through the new path | Legacy reports show the sends correctly |

### Sprint 5: load gate and pilot

| ID | Ticket | Done when |
|---|---|---|
| M3-1 | `fakesendex` built from the M0-4 captures; k6 scenarios | One command per scenario |
| M3-2 | Run the load gate (section 9) against a copy of production with history loaded | All pass; database impact recorded |
| M3-3 | Pilot: 5 to 10 churches added to `pilot_customer`, for four weeks | Exit criteria in section 9 |

### Sprint 6 to 7: one whole code at a time

| ID | Ticket | Done when |
|---|---|---|
| M4-1 | Move every church on the smallest Esendex code; the scheduler now owns that code's rate | Fairness and priority measured on a real Sunday |
| M4-2 | Repeat per code, largest last | Each code holds for two Sundays before the next |
| M4-3 | Estimated finish time and send-by in the compose screen (`HGS-PWA`) | Estimate within 20% of actual |

### Sprint 8 to 9: inbound, other providers, retire

| ID | Ticket | Done when |
|---|---|---|
| M5-1 | Inbound moves to the processor: matcher (4.6), attribution rules (4.7), keywords, narrow START | Seven-day comparison; every difference is a rule in 4.6 or 4.7 |
| M5-2 | Opt-out backfill over `sms_inbound` history | Count reported; spot-checked |
| M5-3 | Nexmo, Sinch and Twilio adapters, or those churches move to an Esendex sender | No SMS left on the VB service |
| M5-4 | Retire the VB SMS path. Voice and email stay | Service no longer handles SMS alerts |

**Rollback at every step from M2-9 on:** delete the church from
`pilot_customer`; legacy picks up its next message. A broadcast in flight
finishes on the side that started it.

---

## 8. Compliance notes that outlive the architecture

- Legacy holds no consent evidence. Modernising in place does not change who is
  texted, so this is not a blocker for the pipeline, but it is a standing legal
  exposure and needs a ruling (Q8).
- AT&T and T-Mobile have moved against shared short codes. Whether ours have a
  written exception is unknown, and it could force churches onto their own
  registered numbers (Q3).
- STOP, START and HELP reply texts must name the program and a contact, per
  sender; the wording needs carrier approval.
- The "which church?" prompt and the triage queue expose message content to
  people outside the church. Both need an access policy and legal review before
  the pilot (Q9).

---

## 9. Load gate and exit criteria

**Load gate** (blocks the pilot). Run on a copy of production with history
loaded, against `fakesendex`.

| Scenario | Load | Pass |
|---|---|---|
| T1 Peak day | Ten times the measured peak day over 24 h, per-code rates as contracted | Standing criteria; product API latency not degraded |
| T2 Sunday shape | Half the day's volume in two hours on one code | No church waits more than twice its fair share; replies handed over in 5 s |
| T3 Whale | One 500,000-person broadcast beside normal traffic | Others slowed by no more than their fair share |
| T4 Chaos | Kill senders mid-call, kill the scheduler leader, fail over the database, provider brownout with late postbacks | Standing criteria after recovery |
| T5 Postback storm with a burst | 2,000 postbacks a second during T2 | Status lag under 30 s; no STOP lost |
| T6 STOP mid-broadcast | STOPs arriving during a send | Nothing sent to a handset more than 10 s after its STOP is stored |
| T7 Midnight and daylight-saving change under load | | Standing criteria |

Standing criteria: every expected recipient ends delivered, failed, rejected,
suppressed, expired or `unknown`; `unknown` only in the dead-worker and timeout
cases and under 1 in 100,000 (a planning figure, Q10); the mock received no
recipient twice; no sender exceeded its rate; nothing sent to an opted-out number.

**Pilot exit:** four weeks; zero duplicate blasts; zero opt-out violations;
delivery rate within one point of legacy; first message handed over in under
5 s; a new admin sends a first message without help in under 10 minutes; pilot
churches would not go back.

---

## 10. Risks

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| The shared database lacks Sunday headroom for the queue workload | Unknown (Q1) | High | M0-1 before anything is built; T1 on a production copy; trigger in section 5 to move `msg` out |
| The carrier rate, not our software, keeps messages waiting | Near certain on busy Sundays | High | Honest estimates and send-by in the UI; capacity as data (4.9); Q2 |
| Carriers do not accept the shared short codes | Unknown (Q3) | Very high | Dedicated numbers are already rows in `sender` |
| Esendex postbacks lack the fields the reconciler needs | Unknown (Q4) | Medium | More `unknown` outcomes, never duplicates; M0-4 first |
| The scheduler leader hangs | Low | High | Heartbeat, alert, session kill, standby (4.8) |
| The legacy hotfix (M0-5) breaks the VB service | Low | High | Smallest possible change; staged; reversible |
| Legacy and new paths share a code during migration, so fairness is partial | Certain for a while | Medium | One whole code at a time; lowest-traffic code first |
| A private reply reaches the wrong church | Happens today | High | Rules in 4.7; no silent guess |
| Another team's work on `HGS-PWA` or the database collides with this | Unknown (Q7) | High | Schema changes limited to the `msg` schema and one stored procedure; coordinate before M2-1 |
| The estimate is wrong for this team | Likely | Medium | Steps are independently shippable; stop after any of them and still be better off |

---

## 11. What is verified and what is not

**Verified in source:** every row in section 2; the legacy reserve statement;
that the PWA sets `alerts.status` to ready; that session-enabled Service Bus
triggers already run in production code; that the Service Bus receiver class is
excluded from the shared library's build; target frameworks of each repo.

**Asserted by the debate and not re-checked:** Service Bus and Azure SQL limits
and pricing; that 2,000-row transactions stay under lock escalation; that
Azure SQL Database exposes no Resource Governor; the write-rate estimates.

**Estimates, unmeasured:** every latency figure in 1.3 and all sprint numbers.

---

## 12. Open questions

Ordered by how much each could change the plan. Each names who can answer.

| # | Question | Why it matters | Who |
|---|---|---|---|
| Q1 | What tier is `schoolcast_live`, and what are its CPU, log-write and blocking figures on a Sunday morning? | Decides whether the queue can live in the shared database (section 5 trigger) | HGS platform / DBA |
| Q2 | What is the contracted rate, in segments per second, on each short code, and what would a higher rate or more codes cost? | It is the real ceiling on how long messages wait | Esendex account rep |
| Q3 | Are our shared short codes approved by the carriers in writing under current rules? | If not, churches need their own registered numbers and the fairness design matters less | Esendex + legal |
| Q4 | Which fields do Esendex `SMSSent` and delivery-receipt postbacks carry (is our `ReferenceID` among them), is there one receipt per message or per segment, and how late can "accepted" arrive? | Decides how many crash and timeout cases end `unknown` | Esendex support; M0-4 |
| Q5 | Can one `AdvancedSMSsend` call carry many different messages, and does Esendex pace or de-duplicate within a call? | Decides how personalised messages are batched | Esendex support; M0-4 |
| Q6 | Where does Esendex send an inbound reply: to the postback URL of the matched message, or to one URL per account or code? | Decides whether inbound can move church by church or only code by code | Esendex support |
| Q7 | Who owns `HGS-PWA` and the legacy database changes, and can they staff the stored-procedure change, the hotfix and the UI work? | Three steps depend on them | Engineering leads |
| Q8 | What is the legal position on texting numbers with no consent record, and which FCC opt-out rule is in force? | Standing exposure; affects STOP wording and matching | Legal |
| Q9 | May platform staff read replies in the triage queue, and may the "which church?" prompt name churches to the handset? | Needed before the pilot | Legal + product |
| Q10 | What rate of `unknown` outcomes is acceptable, and who is told when one happens? | Sets the load-gate ceiling and the support process | Product |
| Q11 | Where does the new code live: a new Azure DevOps repo, or projects inside the `HGS-PWA` solution? | Default is a new repo `HGS-Messaging`; decide before M0-8 | Engineering leads |
| Q12 | Do churches on Nexmo, Sinch and Twilio stay on those providers or move to Esendex? | Sets the size of M5-3 | Product + HGS team |
| Q13 | Which legacy features do churches rely on that touch the send path: translation per recipient, voice-link texts, credits charged per message? | Each must behave the same on the new path | DT-4; HGS team |
| Q14 | Is the priority order emergency, then replies, then broadcasts right, and who may mark a message as an emergency? | Sets lane rules and abuse controls | Product |
