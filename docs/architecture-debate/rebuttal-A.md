# Rebuttal A

## Against B (database as queue)

1. **Its reconciler has no data to read.** B resolves `submitted` batches by "matching postbacks by reference", but leaves the callback path untouched. The legacy receipt branch never reads `ReferenceID` (`cdyne_sms_response.aspx.vb:69-124`; only the inbound branch does, `:143`). For a batch whose worker died, there is no `text_message_response` row, so the handler inserts an orphan keyed by provider MessageID (`:189`). Scenario: a worker dies after Esendex accepts 2,000 recipients; every one ends `unknown`, although the receipts arrived.
2. **The 60-second sleep stays.** B writes `text_message_response` after the provider responds; a receipt that arrives first still blocks a web thread for 60 s (`:227`). At carrier rate on a Sunday that is the same App Service that must accept STOP.
3. **A hung scheduler stops every church.** The standby takes the `sp_getapplock` lock only when the holder's connection dies. A process that stalls with its connection open keeps the lock, and nothing sends. B names no watchdog.

**What B gets right that I lack:** suppression, state change and attempt record in one transaction; budget as contracted rate minus in-flight segments, which bounds Esendex's own queue better than my 20% reserve; a resumable paged fan-out; and the finding that a SOAP exception is swallowed with no record (`PrimeMessageSender.vb:264-270`). B is also right that my sender side is its design plus a wake-up.

## Against C (Postgres and Event Hubs)

1. **The projector lag can file a private reply under the wrong church.** Inbound stays on legacy until C's step 4, and legacy attribution reads `call_queue` (`AppCode\utilities.vb:781-783`). C's target lag into `call_queue` is 30 s. Scenario: a pilot church texts a member, who replies in 15 s; the row is not there yet, so the reply attaches to the newest older two-way message, possibly another church's. That is one of the brief's four worst mistakes.
2. **Change Tracking will miss scheduled sends.** `alerts` carries `scheduledate` (`HGS.Providers\Database\Records\AlertRecord.cs:19`). An alert saved Thursday for Sunday 8am produces no change at 8am, so the bridge needs a time poll anyway. Change Tracking is not used anywhere in these repos, and enabling it is the shared-database schema change C's own objection warns against.
3. **The mirror buys nothing and adds a stop condition.** C still queries legacy `sms_optout` once per batch, so the hot path reads legacy regardless. A stale watermark halts all broadcast lanes; with a single bridge replica, a restart on Sunday morning is an outage. The version number does not advance when nobody opts out, so "stale" needs a heartbeat C does not describe.
4. **Platform load for three engineers.** Postgres, Event Hubs, Container Apps, ACR and Bicep are all new; no Dockerfile exists in the four repos.

**What C gets right that I lack:** setting our own `StatusPostBackURL` per request (`PrimeMessageSender.vb:258`), so new sends never touch the Web Form; a shadow fan-out diffed against `call_queue`; and the finding that the legacy topic publish sets `MessageId = context.GetHashCode()` (`AzureServicesClient.vb:118`, confirmed), which defeats duplicate detection.

## My amendments

- Replace the 20% reserve with B's rule: budget = rate minus in-flight, in-flight capped at about 5 s, strict lanes.
- Make `send_batch` a row and do suppression, claim and attempt in one transaction, as B does.
- New sends use their own postback URL (from C), going straight to a Function that publishes to Service Bus. Step 1 no longer depends on the legacy switch; if it is used, fix the `MessageId` first.
- Add C's shadow fan-out before the pilot church.
- Projection into `call_queue` is written in the claim transaction, not asynchronously, so legacy attribution sees it.

**Held firm:**
- One store. Truth stays in Azure SQL; no mirror, no projector lag.
- Service Bus for callbacks and wake-up. Callbacks arrive at two to three per message in bursts and must be absorbed, ordered per handset, retried with delay and dead-lettered. That is where B has nothing and where Event Hubs has no per-message retry or dead-letter.
- No single leader. Church tokens plus an atomic bucket row survive any one worker hanging.

## One element from each

B's single transaction for suppression, claim and attempt; C's own postback URL per request; and my Service Bus path for callbacks and wake-up.
