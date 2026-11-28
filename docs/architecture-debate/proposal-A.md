# Proposal A: Service Bus moves the work, Azure SQL holds the truth

**Principle.** Every Service Bus message is a small pointer ("expand alert 123", "church 45 has work on sender 22383"). Recipient state lives only in Azure SQL. So a lost, duplicated or redelivered message can never cause a lost or duplicate text; the broker supplies push wake-up, competing consumers, locks, dead-lettering, scheduling and the autoscale signal.

Verified in code (paths under `Legacy code\`):
- Session-enabled Service Bus triggers already run in production: `HGS-HGSCoreApi\HgsCoreApi.CloudFunctions\Functions\EventHandlers\ProcessSinchSmsEvent.cs:15`.
- The callback-to-topic switch exists: `cdyne_sms_response.aspx.vb:31-36`. It swallows every failure (`:38-40`), and the shared publisher returns `false` on `ServiceBusException` (`ServiceBusMessagePublisher.cs:42-45`). The receiver class is excluded from the build (`HighgroundProviders.Azure.ServiceBus.csproj:15`). Both need fixing.
- The library targets netstandard2.0, so the .NET Framework 4.7.1 service can publish with it.
- Duplicate-blast bug: a result-write error sleeps 30 s and re-sends the batch (`PrimeMessageSender.vb:279-283`).
- The opt-out check returns "not opted out" on a parse failure (`CallQueue.vb:3316-3329`).
- HGS-PWA is net10.0, deployed by zip to App Service from Azure DevOps (`Pipelines\azure-pipelines-release.yml`); its Kafka Function is .NET 10. No Dockerfile exists in the four repos.

## 1. Components

| Stage | Fed by | SQL reads / writes | Scale |
|---|---|---|---|
| Dispatcher | 1 s timer | Reserves due `alerts` with the legacy statement (`status=1 -> 2`, `MessagingServiceCore.vb:2582`); publishes `expand` | Single; the reserve makes extras harmless |
| Expander | queue `expand` | One set-based `INSERT...SELECT` into new `sms_send_item` (unique `alert_id, phone`), excluding opt-outs; also `call_queue`, `call_recipients` for existing reports. Publishes one church token | Competing consumers |
| Sender | queues `send-priority`, `send-bulk` | Takes tokens from `sender_bucket`; claims up to 200 segments of that church's queued rows; commits `submitted`; calls Esendex; re-queues the token if rows remain | Competing consumers; throughput is carrier-bound |
| Recorder | queue `send-results` | Bulk upsert of the provider response into `text_message_response` by `message_id` | Competing consumers |
| Callback processor | topic `provider-callbacks`: subscription `receipts` (session = provider MessageID), `inbound` (session = handset) | Forward-only status update; inbound attribution per PLAN 5.3.1 | One session per handset keeps order |
| Sweeper | 30 s timer | Re-publishes tokens for churches with queued rows and no progress; releases stale `claimed`; flags stale `submitted` | Single |

A receipt that beats its result is rescheduled five seconds out with a scheduled message, replacing `Thread.Sleep(60000)` (`cdyne_sms_response.aspx.vb:227`).

## 2. STOP

The webhook writes `sms_optout` synchronously before answering, as today; STOP is never queued behind anything. The claim is one statement, `UPDATE ... WHERE status='queued' AND NOT EXISTS (opt-out for this phone on any sender the church has used, or code 0)`, run immediately before each provider call. A parse failure fails closed. Mid-broadcast exposure is one slice already handed over: at most 200 segments, about two seconds. Nothing we build can recall those.

## 3. Duplicates and losses

| Event | Outcome | Key |
|---|---|---|
| `expand` redelivered | Insert adds nothing | Unique `(alert_id, phone)`; `MessageId = expand:{alertId}` with duplicate detection as a first filter |
| Token redelivered or duplicated | Second claim returns different rows or none | Row claim, `queued -> claimed`, fenced by attempt number |
| Worker dies after Esendex accepted | Rows stay `submitted`; **never auto-resent**; reconciled from postbacks, else marked `unknown` and alerted | `ReferenceID = {alert}-{batch}-{attempt}` (legacy already sends a ReferenceID, `PrimeMessageSender.vb:257`) |
| Provider call times out | Same as above | Same |
| Result write fails | Message retries, then dead-letters; no resend | `message_id` upsert |
| Receipt redelivered | No-op | Provider MessageID, forward-only status |

Unverified: whether Esendex delivery receipts carry `ReferenceID`. Inbound replies do (`cdyne_sms_response.aspx.vb:143`). If receipts do not, more rows end `unknown`.

## 4. Rate, fairness, priority

Service Bus has no rate limiter and FIFO queues are not fair, so neither is left to the broker.
- **Rate:** one row per sender in `sender_bucket`, refilled at the contracted segments per second, drawn by one atomic `UPDATE`. Works across any number of workers; no Redis.
- **Fairness:** exactly one token per church per sender in `send-bulk`. Each visit sends at most two seconds of rate, then the token goes to the tail. FIFO over tokens is round-robin over churches.
- **Priority:** emergencies and staff replies use `send-priority`; bulk may not draw the bucket below a 20% reserve. Worst wait behind a mass send is one slice.
- **Over capacity:** backlog stays as queued rows in SQL, not inside Esendex, so STOP and priority still apply. Compose shows an ETA; rows past their `not_after` are skipped and reported, not delivered at 11.

## 5. Migration

1. **Weeks 1-2:** turn on the existing topic switch; a new Function consumes `receipts` in shadow mode and diffs against legacy. Rollback: flag off.
2. Function becomes authoritative for receipts; the 60 s sleep goes. Inbound stays in the Web Form.
3. The VB service publishes provider responses to `send-results` instead of writing inline; the resend-on-error goes. One small VB change, flagged.
4. One pilot church on the new send path through a per-customer flag that `sp_Messaging_GetAlerts` excludes. Rollback: clear the flag.
5. Ramp by cohort, then move inbound, then retire the service.

During steps 4-5 legacy still sends unpaced, so the new bucket is set to a fraction of the rate.

## 6. Latency (estimates, unmeasured)

- **500 people:** dispatch within 1 s, expand under 1 s, first handoff in 2-3 s, last in about 6 s for single-segment text.
- **100,000 people:** expand 5-20 s; first handoff under 30 s. The last is set by the carrier: 100,000 segments at 100 per second is about 17 minutes alone on the code, double for two segments, longer when shared. Our overhead is under a minute.

## 7. Objections

1. **"Rate, fairness and idempotency all live in SQL, so the broker is decoration and a second thing to operate."** Partly true, and it costs a sweeper. The broker removes the 10 s poll, gives lock and redelivery, dead-letter alerting, scheduled retry, per-handset ordering and a scale signal, all of which we would otherwise hand-build. The team already runs it.
2. **"The token-per-church loop is a hand-rolled scheduler; a lost token stalls a church."** The sweeper re-seeds within 30 s, and a duplicate token is harmless because claims are in SQL. It is about one screen of code, and it is the load test's first target.

## 8. Stack

- C# on .NET 10 (LTS), matching HGS-PWA.
- Azure Functions, isolated worker, Elastic Premium plan with one always-ready instance (no cold start on Sunday).
- Azure Service Bus Standard; move to Premium if throttling shows in the load test.
- Azure SQL `schoolcast_live`; Dapper for the claim and bulk statements.
- No cache.
- Application Insights with OpenTelemetry; alerts on dead-letter depth, oldest queued row, `unknown` count.
- Azure DevOps pipelines, zip deploy with slots.

Service Bus limits relied on (five-minute maximum lock, sessions and duplicate detection on Standard) are from Microsoft Learn as I recall them; not re-fetched this session. Pricing not confirmed.
