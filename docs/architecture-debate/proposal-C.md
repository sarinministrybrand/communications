# Proposal C: a delivery pipeline with its own store

**Thesis.** The carrier caps throughput; the shared database causes the failures. Each recipient costs about six synchronous statements on `schoolcast_live`: an opt-out select (`CallQueue.vb:2534`), a `call_queue` insert (2548), a recipient insert (2553), and a select plus insert into `text_message_response` (`PrimeMessageSender.vb:474-482`). Each receipt adds a select, a possible 60 s sleep and an update (`cdyne_sms_response.aspx.vb:177-227`). The claim, lease and fairness state that real queues need would land on the same database. Legacy stays the record for churches, people, alerts and opt-outs. The hot path moves off it.

## 1. Components

| Stage | Fed by | Reads / writes | Scale |
|---|---|---|---|
| Bridge | Change Tracking on `alerts`, every 1 s | reserves the alert in legacy; inserts `broadcast`, unique `legacy_alert_id` | 1 |
| Fan-out | `broadcast` | runs the legacy audience SQL once, set-based (`CallQueue.vb:2421-2462`); `COPY` into `delivery`, unique `(broadcast_id, phone)` | N, `SKIP LOCKED` |
| Scheduler | queued `delivery` rows | per-sender token bucket, round-robin by church, lanes; writes `send_batch` | 1 leader (advisory lock) |
| Senders | `send_batch` | provider call; `delivery_attempt` | N |
| Postback webhook | Esendex, through our own `StatusPostBackURL` (set per request, `PrimeMessageSender.vb:258`) | Event Hubs `sms-postbacks`, then 200 | N |
| Status writer | `sms-postbacks` | forward-only Postgres update; outbox to `delivery-events` | per partition |
| Legacy projector | `delivery-events` | `SqlBulkCopy` to staging, then `MERGE` into `call_queue`, `call_recipients`, `text_message_response` | per partition |
| Opt-out mirror | Change Tracking on `sms_optout` | upserts Postgres `optout` | 1 |

The projector writes the columns the VB service writes today, so reports are unchanged. Lag target: under 30 s, shown on screen.

## 2. STOP
- Postgres `optout` only adds suppression. The send gate is the union of Postgres and legacy.
- Opt-out is checked when each batch is claimed, not at fan-out.
- Until inbound moves off legacy, each batch also runs one TVP query against legacy `sms_optout`: one read per batch.
- If the Change Tracking watermark is more than 60 s stale, broadcast lanes stop (fail closed).
- **Mid-broadcast:** a STOP committed at time t blocks every batch claimed after t. The only exposure is what Esendex already holds, capped at about 5 s of rate.
- Once inbound moves (step 4), the new webhook commits a STOP to both stores before it answers 200.
- Legacy sends when a number fails to parse (`CallQueue.vb:3316-3329`). We do not send.

## 3. Duplicates and losses

| Case | Key / mechanism |
|---|---|
| Alert picked up twice | unique `legacy_alert_id` |
| Fan-out rerun | `ON CONFLICT (broadcast_id, phone) DO NOTHING` |
| Worker dies after acceptance | `submitted` and the attempt number are committed **before** the HTTP call. Only the reconciler resolves them, matching postbacks on `ReferenceID={broadcast}-{batch}-{attempt}` plus `ToPhoneNumber`. Rows are never resent on elapsed time alone |
| Provider timeout | the rows become `unknown`, an alert fires, there is no automatic resend |
| Event redelivered | updates keyed on provider `MessageID` move forward only, so replay is a no-op; the projector `MERGE`s on `message_id`. Legacy sets the Service Bus `MessageId` to `context.GetHashCode()` (`AzureServicesClient.vb:118`), which is not stable, so duplicate detection is defeated |

We drop legacy's whole-batch resend after a result-write error (`PrimeMessageSender.vb:280-283`). Unconfirmed (PLAN D2): whether receipts carry `ReferenceID`. Legacy reads it only on inbound (`cdyne_sms_response.aspx.vb:143`).

## 4. Rate, fairness, priority
One scheduler per sender counts segments. Lanes are strict: emergency, then one-to-one replies, then broadcasts, with deficit round-robin by church inside the broadcast lane. In-flight sends are capped at about 5 s of rate, so a staff reply waits at most about 5 s.

When demand exceeds the carrier rate, the backlog waits in Postgres. Compose shows an ETA: segments ahead divided by rate. Quiet hours are rechecked at every claim. A broadcast past its send-by time pauses and alerts the church rather than arriving at 11. Nothing here raises the rate. More codes are rows in `sender`.

## 5. Migration
1. **Weeks 1-2, ships:** provision Postgres; turn on Change Tracking; import opt-outs; run a shadow fan-out of every real alert with no send, diffed daily against `call_queue`. It reverses by switching off.
2. One internal customer sends on a dedicated long code through the projector; check the legacy reports.
3. Pilot churches are listed in an `sms_pipeline_customer` table, and `sp_Messaging_GetAlerts` skips them. Rollback is deleting the row. A broadcast in flight finishes on the side that started it.
4. Point Esendex inbound at the new webhook.
5. Expand by cohort, then add Nexmo, Sinch and Twilio. The VB SMS path is retired last.

**Why this is not the rewrite.** The database, UI, PWA, reports and data model stay. Only the SMS send loop and its receipt writes are replaced, behind a per-customer flag. The Python prototype has its own church and person tables, so adopting it would be the rewrite. I port only its partitioned `delivery` table and its fan-out/send split to .NET. Its `send_one` has no send-time opt-out check, rate limit or attempt fencing (`app/worker.py:109-166`).

## 6. Latency (estimates; the load test confirms)

| | 500 | 100,000 |
|---|---|---|
| Pickup | ≤1 s (legacy polls every 10 s) | ≤1 s |
| Audience and `COPY` | <1 s | 5-15 s (assumed) |
| First message handed to provider | ~2 s | ~15 s |
| Last, code idle at 100 msg/s | ~7 s | ~17 min, carrier-bound |
| Last, 10 churches competing | ~50 s | longer; small sends never wait behind it |

## 7. Objections

**"At 100 msg/s Azure SQL copes; the carrier is the limit."** For raw throughput, yes. But the queue state machine would mean schema changes and heavy write churn on a database every app shares (HGS-PWA `AGENTS.md` forbids uncoordinated `MyCastDb` schema changes). A Sunday blast would compete with live screens. One extra store removes about six statements per recipient and keeps up when more codes raise the rate.

**"Two opt-out stores means split brain."** Each fact has one writer, and Postgres can only add suppression. The gate is a union and fails closed when the watermark lags. The real cost is a second store to operate, which is why there is no Redis and no self-run Kafka.

## 8. Stack
- .NET 10 (C# 14): Worker Service and ASP.NET Core; Npgsql, Microsoft.Data.SqlClient, Confluent.Kafka (already used in `HGS.Amplify.Sync.Function`)
- Azure Container Apps, with KEDA scaling on Event Hubs lag; the scheduler is a single replica
- Azure Event Hubs Standard, Kafka endpoint, 2 TU, 7-day retention (limits from memory, unconfirmed)
- Azure Database for PostgreSQL Flexible Server 16 with zone-redundant HA (whether its standby is synchronous is unconfirmed)
- Azure SQL Change Tracking (tier support unconfirmed)
- No cache
- OpenTelemetry to Application Insights; alerts on oldest-queued age, watermark lag, projector lag and the `unknown` count
- Azure DevOps Pipelines, Bicep, ACR
