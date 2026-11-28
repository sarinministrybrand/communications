# Rebuttal B

## 1. Where the rivals fail

### Proposal A (Service Bus + Functions)

**A1. The result hop breaks A's own "pointers only" rule.**

- Migration step 3 has the VB service publish provider responses to `send-results`. That message is the only copy of the Esendex message ids.
- The shared publisher swallows failure: it catches `ServiceBusException` and returns `false` (`ServiceBusMessagePublisher.cs:42-44`, checked).
- A itself plans Standard tier, "Premium if throttling shows".
- Failure scenario: Sunday throttling drops one publish. `text_message_response` is never written for 2,000 recipients. Every receipt for them misses its `message_id` lookup and sleeps twice for 60 s (`cdyne_sms_response.aspx.vb:226-227`). Those rows end `unknown`, and someone has to review 2,000 rows by hand.
- My design writes the provider response in the same database as the `submitted` row, with no hop in between.

**A2. The 20% priority reserve throttles every broadcast, all the time.**

- On Sunday morning, priority traffic is close to zero. With bulk capped at 80% of the rate, a 100,000-segment send takes about 21 minutes instead of 17. That makes the "8am arriving late" failure worse.
- Strict lanes plus a 5 s in-flight cap (my design, and C's) give the same bound on priority wait without the permanent 20% loss.

**A3. Rate limiting and fairness live in SQL anyway.**

- A already keeps the `sender_bucket` row and the claims in SQL. On top of that it adds a sweeper that re-seeds lost church tokens, up to 30 s per stall.
- So A runs Service Bus, Elastic Premium and a token-repair loop, and still gets the correctness from SQL.

### Proposal C (Postgres + Event Hubs, projected back)

**C1. Projection lag misfiles private replies.**

- Inbound two-way attribution in legacy is `SELECT TOP 1 call_queue.id ... WHERE phone_number=@p AND alert_2way_sms=1 ORDER BY call_queue.id DESC` (`AppCode/utilities.vb:781-792`, checked).
- C fills `call_queue` asynchronously through a projector with a 30 s lag target. Inbound stays on legacy until C's step 4.
- Failure scenario: a member replies 10 s after a pilot church's two-way text. The projector hasn't run, so the reply is filed under whichever church last sent them a two-way text. That is the brief's "private reply filed under the wrong church".
- A late `MERGE` also hands out identity values in projection order, not send order. That corrupts "most recent" even after the projector catches up.

**C2. Fail-closed on watermark lag stops broadcasts.**

- If the Change Tracking watermark on `sms_optout` is more than 60 s stale, every broadcast lane stops.
- Failure scenario: one stalled Change Tracking poll at 7:55 on Sunday halts every church's broadcast.
- C still runs a per-batch query against legacy `sms_optout` until step 4. The hot path therefore isn't off the shared database yet, and the mirror adds a second way to fail.

**C3. C's first step loads the database it says it protects.**

- Weeks 1-2 run a shadow fan-out of every real alert. That repeats the heaviest legacy query (`CallQueue.vb:2421-2462`) on `schoolcast_live` during the Sunday peak.
- Enabling Change Tracking on `alerts` and `sms_optout` is also a schema change on that shared database.
- C adds Postgres, Event Hubs, Container Apps, ACR and Bicep for a 2-3 person team. None of the repos has a Dockerfile (A's check and my glob agree).
- C leaves the Postgres standby's synchronous status unconfirmed. PLAN 5.2 item 9 depends on that standby.

## 2. What they get right that I lacked

**From A:**
- Receipts first, as the opening migration step. It removes the 60 s sleep with a scheduled retry and has low risk.
- A `not_after` send-by time.
- Per-handset ordering for inbound messages.
- Visible dead-letter depth.

**From C:**
- Our own postback endpoint, set per request through `StatusPostBackURL` (`PrimeMessageSender.vb:258`, checked).
- Quiet hours rechecked at every claim.
- A daily shadow diff of our recipients against `call_queue` as the release gate.
- The finding that legacy's Service Bus `MessageId` is `GetHashCode()`, so duplicate detection never works.

My proposal had no send-by deadline and no quiet hours. Both were real gaps.

## 3. My amended proposal

**I would add:**
1. **Send-by and quiet hours.** `delivery.not_after` and quiet hours are checked in the same submit transaction as opt-out. A row past its deadline pauses its broadcast and alerts the church; nothing is delivered late in silence.
2. **Postback table.** A new `msg.postback` endpoint, set through `StatusPostBackURL`, inserts the raw postback into a SQL table and only then answers 200. That table replaces the 60 s sleep.
3. **New migration order.** Weeks 1-2 become: create the `msg` schema, add an empty pilot table and edit the stored procedure, remove the VB resend, and capture postbacks in shadow, diffed against legacy. The send path moves after that.
4. **Shadow fan-out only off-peak, on a sample.** Each pilot church then has to pass a recipient diff against `call_queue` before it moves.
5. **Lossy Service Bus ping from day one,** sent by the dispatcher after commit. It cuts idle latency. Losing a ping loses nothing.

**I hold firm on:**
- **One store.** The opt-out suppression, the `submitted` row and the legacy `call_queue` and `text_message_response` writes all commit in one Azure SQL transaction. C1 and A1 show what breaks when they are split.
- **Strict lanes with a 5 s in-flight cap,** rather than a percentage reserve.
- **No broker in the correctness path.**

Azure SQL's built-in HA covers failover. I believe a committed send doesn't come back after failover, but that is from memory and not re-verified.

## 4. One element from each

From B: the single Azure SQL transaction that suppresses opt-outs, marks rows `submitted` and writes `call_queue`. From A: receipts first, with a scheduled retry replacing `Thread.Sleep`. From C: our own `StatusPostBackURL` endpoint plus a send-by deadline that pauses rather than delivers late.
