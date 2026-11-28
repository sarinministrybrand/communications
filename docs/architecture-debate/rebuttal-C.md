# Rebuttal C

## Against A (Service Bus pointers, state in Azure SQL)

**1. The receipt feed it builds on cannot do what A says.** A gives the `receipts` subscription "session = provider MessageID". Publishers set sessions. The legacy publisher sets `SessionId` to `{provider}-{service}`, i.e. `"cdyne-sms"`, and overrides it only when a `QueueId` or `ReferenceId` parameter is present (`AzureServicesClient.vb:72-111`). Receipts that do not carry `ReferenceId`, which is unconfirmed (D2), therefore all land in one session. One session means one consumer, so a 100k Sunday send would push up to 200k receipts through a single serial consumer. In addition, `MessageId = context.GetHashCode()` (`:118`) defeats duplicate detection, and the publish call is a swallowed `Async Sub` (`cdyne_sms_response.aspx.vb:31-40`). A's step 1 diff would mostly measure publisher loss.

**2. The set-based expander blocks legacy sends.** A's expander writes `call_queue` and `call_recipients` in one `INSERT...SELECT` per alert. For 100k recipients, that is far beyond SQL Server's roughly 5,000-lock escalation point. The default `LOCK_ESCALATION=TABLE` then takes a table lock on `call_queue` while the VB service is still inserting row by row for every non-pilot church (`CallQueue.vb:2548`). Result: one big Sunday send stalls every other church's legacy send for the length of the transaction.

**3. The 20% priority reserve wastes carrier time.** As written, it is a hard floor on bulk, not a reserve that bulk can borrow when priority traffic is idle. On a 400k Sunday with no emergencies, the code runs at 80 msg/s, so the send takes 83 minutes instead of 67.

**What A gets right that I lack.** It moves receipts first: the 60 s sleep goes before any send logic changes, which is the cheapest early win. It reuses the legacy reserve statement (`MessagingServiceCore.vb:2582`) instead of my Change Tracking bridge. Its code citations check out (`ProcessSinchSmsEvent.cs:15`, `ServiceBusMessagePublisher.cs:42-44`, `csproj:15`).

## Against B (Azure SQL as the queue, `msg` schema)

**1. Its write budget leaves out receipts.** B counts "about 4 row writes per message". The receipt path stays in the Web Form: per receipt a select, a possible 60 s `Thread.Sleep`, then an update or insert (`cdyne_sms_response.aspx.vb:177-227`). With up to two receipts per message that roughly doubles the statements per message, and B's design never changes it. When a receipt beats B's per-batch `text_message_response` write, the handler inserts its own row (`:189`), B's write then lands a second one, and reports double-count.

**2. There is no isolation.** B concedes that Azure SQL Database does not expose Resource Governor. The scheduler's 500 ms poll and the sender claims share CPU and log with every PWA screen and report on `schoolcast_live`. A heavy report at 8am Sunday slows sends. A load test on a copy measures that risk; it does not remove it.

**3. Retention is a chunked `DELETE` of millions of rows a month.** The deletes generate log on the shared database. Postgres detaches a day in O(1).

**What B gets right that I lack.** The opt-out suppression and the move to `submitted` commit in one transaction against the authoritative `sms_optout`, with no mirror lag. Its week 1 deletes the 30 s resend from the VB sender. It migrates by whole short code so the scheduler actually owns each code's rate. It adds no new store.

## Amended C

**Changes:**
1. Drop the Change Tracking bridge for alerts. Reserve with the legacy statement (`MessagingServiceCore.vb:2582`), as A and B do.
2. Drop Event Hubs in v1. Raw postbacks go to an append-only Postgres `postback` table before the webhook answers 200. The projector reads a Postgres outbox.3. Make the claim-time opt-out check B's: one set-based query against legacy `sms_optout` per batch, inside the claim. The Postgres mirror becomes a second fence, not the source.
4. Step 1 also ships B's VB hotfix (delete the 30 s resend) and A's receipts-first move: our webhook owns receipts for pipeline sends, through the per-request `StatusPostBackURL` (`PrimeMessageSender.vb:258`).
5. Migrate by whole short code (B).

**Held firm:**
- Per-recipient state, receipts and retention stay off `schoolcast_live`; they are most of the writes.
- Legacy tables are fed by a set-based, paged `MERGE` projector, so no transaction crosses the escalation threshold.

## One element from each
A's receipts-first migration, B's atomic suppress-and-submit check against legacy `sms_optout`, and C's separate delivery store with a paged projector back into the legacy tables.
