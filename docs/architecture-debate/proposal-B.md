# Proposal B: the database is the queue of record

**Thesis.** The short code caps us at about 100 segments/s per code, so a broker can't make a 100k send finish sooner. What hurts today is missing state, not missing infrastructure. Legacy already uses the table-claim pattern: the PWA sets `alerts.status = Ready` (`HGS.Core/AlertService.cs:155`), and the VB service claims with `update alerts set status=2 ... where status = 1` (`MessagingServiceCore.vb:2582`). We keep that outbox and make each later step a claimable row.

## 1. Components (new schema `msg` in `schoolcast_live`)

| Stage | Claims from | Writes | Scale |
|---|---|---|---|
| Fan-out worker | `msg.broadcast_job` (alert_id PK, lease_until, cursor), created by claiming `alerts` status 1 | Set-based `INSERT msg.delivery` in pages of 2,000 contacts, keyed by contact id. The cursor makes it resumable | N stateless instances |
| Scheduler (one at a time, `sp_getapplock` session lock) | Every 500 ms it reads `msg.delivery` status=queued | `msg.send_batch` rows sized to each sender's spare budget | Standby instance takes the lock when the holder's connection dies |
| Sender | `msg.send_batch` with `UPDLOCK, READPAST, ROWLOCK` | `delivery_attempt`, then status; legacy `call_queue`, `call_recipients`, `text_message_response` as one set-based write per batch | N stateless instances |
| Reconciler | `send_batch` status=submitted and older than T | Matches postbacks; resolves rows to accepted or `unknown` | 1 |

Indexes:
- `delivery`: unique `(alert_id, phone)`, plus filtered `IX_queued (sender_id, lane, customer_id, id) WHERE status=0`.
- `send_batch`: filtered `(status, lane, id) WHERE status IN (0,1)`.
- `delivery_attempt`: unique `reference_id`.

Each claim is an index seek on a small filtered index.

**Protecting the shared DB.** The write rate is bounded by the carrier. At 100 msg/s and about 4 row writes per message, that's roughly 400 writes/s per code. Fan-out bursts are paged. Every transaction stays under 2,000 rows, below the 5,000-lock escalation threshold. READPAST means a claim never waits on a lock. The new tables have no FKs into legacy tables. One hot legacy read remains: the `sms_optout` probe, which needs an index on `(number_dialed, short_code)`. I haven't confirmed that index exists; if it doesn't, create it with `ONLINE=ON`.

Log: General Purpose caps log at 9 MiB/s on 2 vCores and 50 MiB/s on 12 or more ([MS Learn](https://learn.microsoft.com/en-us/azure/azure-sql/database/resource-limits-vcore-single-databases)). My estimate is under 1 MiB/s at carrier rate. That is unmeasured, and the database tier is unknown.

Retention is a chunked delete run off-peak.

## 2. STOP is never texted again

- The legacy handler writes `sms_optout` (`AppCode/utilities.vb:477`) and keeps doing so.
- The sender moves a batch from claimed to submitted in one transaction. That transaction first sets rows to `suppressed` where a matching `sms_optout` row exists for any sender the church has ever used, or with code 0. Only the rows that survive go out.
- A STOP that arrives mid-broadcast therefore stops every batch not yet submitted. The exposure is the in-flight cap of about 5 s of rate. Legacy checks opt-outs only once, at fan-out (`CallQueue.vb:2534`).
- Phones are stored as `bigint`. A number that won't parse is never inserted, so the check fails closed. Legacy fails open (`CallQueue.vb:3316-3329`).

## 3. Duplicates and losses

- **Fan-out is idempotent** on the unique `(alert_id, phone)`. A re-run after a crash inserts nothing twice.
- **Provider call.** `submitted` and `attempt+1` are committed before the SOAP call. The `ReferenceID` is `{batch_id}-{attempt}`; legacy already sends a `ReferenceID` (`PrimeMessageSender.vb:257`). Our idempotency key is the unique `(batch_id, attempt)` in `delivery_attempt`. Nothing provider-side is idempotent: whether Esendex de-duplicates is unknown (PLAN D2).
- **Worker dies after Esendex accepted.** The batch stays `submitted`. Leases cover only `claimed`, so no one resends it. The reconciler matches postbacks by reference. Rows with no evidence become `unknown`, raise an alert, and are not resent automatically (PLAN 5.2).
- **Lease expires during a slow call.** This can't cause a resend, because the call happens only after `submitted`. A late result write is fenced with `WHERE id=@b AND attempt=@a AND status=submitted`.
- **Timeout.** Same path as the dead worker: unknown, never blind-retried. Only an explicit provider rejection retries, as a new attempt.
- **Two legacy bugs removed.** A result-write error re-sends the whole batch after a 30 s sleep (`PrimeMessageSender.vb:280-283`), causing duplicates. A SOAP exception is swallowed with no record (`:264-270`), causing silent loss.

## 4. Rate, fairness, priority

- Only the scheduler spends budget, so there is no distributed token bucket.
- Per sender: budget = contracted segments/s minus in-flight segments. In-flight is capped at about 5 s of rate, so Esendex's own queue stays short.
- Lanes: emergency, then one-to-one replies, then broadcasts. Within a lane, deficit round-robin across churches.
- A staff reply waits one scheduler tick plus one sender poll, about 1 s.
- **When demand exceeds the carrier rate**, rows wait in `delivery` as queued, not inside Esendex. The compose screen shows the estimated window, computed from queue depth divided by rate.
- **During coexistence**, legacy still pushes 2,000-recipient batches into Esendex unthrottled, so fairness on a code is best-effort until that whole code has migrated.

## 5. Migration (each step reversible)

1. **Weeks 1-2, shipped:**
   - Create the `msg` schema and the `msg.pilot_customer` table, empty at first.
   - Edit `sp_Messaging_GetAlerts` to skip pilot customers. Its body isn't in the repo; read it from the database first. With the table empty, this changes nothing.
   - VB hotfix: delete the 30 s resend.
   - Add an App Insights dashboard of `alerts` queued time.
2. **Weeks 3-6:** the worker sends for internal test customers only.
3. One-to-one replies move to the new path for pilot churches.
4. Cohorts within the smallest brand short code first, then whole codes, so the scheduler owns each code's rate.
5. Retire the VB SMS path. Voice and email stay on VB.

**Rollback:** delete a customer from `pilot_customer` and legacy picks up their next alert. Broadcasts already in flight finish where they started; handing them back risks duplicates. A `msg.control.pause` flag mirrors `comm_control` (`MessagingServiceCore.vb:2801`).

## 6. Latency, send pressed to handed to the provider

- **500 recipients, idle code:** about 1 s to poll and claim, under 1 s to fan out, then 500 / 100/s = 5 s. Total about 6 s.
- **100,000 recipients, single-segment:** about 17 min at 100/s if alone; 2 segments doubles it. If three other churches are sending, round-robin roughly quadruples it.

Legacy reports "handed off" sooner because it dumps 2,000 recipients per call into Esendex. Delivery is still carrier-paced, and a staff reply then queues behind the dump. Assumption: 100/s contracted. This is unverified.

## 7. Strongest objections

- **"The shared database is a blast radius."** A bad legacy report could slow sends, and sends could slow the PWA. Answer: write volume is capped by the carrier (at most about 500 writes/s across 5 codes, at 100/s each). Claims never block, because of READPAST. The Phase 2 load gate runs against a copy of production with history loaded. `msg` has no FKs into legacy, so it can move to its own database later. The `sms_optout` join is the one coupling, and it would then need replication.
- **"Polling isn't a real queue."** Answer: a leased row is a stronger queue for this system. Opt-out suppression, the state change and the attempt record commit atomically, which a broker can't do without an outbox anyway. Polling costs about 10 seeks/s. If that cost ever shows up, a lossy Service Bus ping (existing `HighgroundProviders.Azure.ServiceBus`) cuts idle latency with no change to correctness.

## 8. Stack

| Concern | Choice |
|---|---|
| Language | C# on .NET 10, matching `HGS.Api.csproj` |
| Hosting | Generic Host `BackgroundService` on its own App Service plan (Always On), scale out by instance count |
| Store | Existing Azure SQL `schoolcast_live`, schema `msg`; `Microsoft.Data.SqlClient` 5.2.3 (already in `HGS.Providers`) for claim SQL |
| Wake-up | None in v1 (500 ms poll); optional lossy Service Bus ping later |
| Provider client | Esendex SOAP via `System.ServiceModel.Http` (new package, needs approval) |
| Observability | Application Insights (already in CoreApi Functions): queued age per lane, budget used, unknown count |
| CI/CD | Azure DevOps YAML following `HGS-PWA/Pipelines/azure-pipelines-release.yml`; idempotent SQL migration scripts as a pipeline step |

Unverified: the database tier and its Sunday headroom, the `sms_optout` index, the contracted rate, Esendex postback fields, whether one `AdvancedSMSsend` call accepts per-recipient messages (it would end legacy's batch size of 1 for merge fields, `CallQueue.vb:2383-2386`), and Resource Governor isolation, which Azure SQL Database doesn't expose to users (from memory).
