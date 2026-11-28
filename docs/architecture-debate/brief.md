# Architecture debate brief: modernising the HGS church messaging send path

## The job
A US church-messaging business sends millions of SMS a month through a legacy
system. The owner has decided: **modernise the legacy stack in place; it is not
a rewrite.** Goals, in the owner's words: a scalable solution, real queues,
messages must not sit waiting in queues for long. The architect must finalise
the tech stack and the plan.

## Hard constraints
- The system works today on a **shared short code**: many churches send from the
  same sender number. Legacy code actually shows several short codes by brand
  (22383, 34166, 29988, 31263, 39256) plus some dedicated numbers, and three or
  four providers (Esendex/CDYNE is the main one; Nexmo, Sinch, Twilio paths
  exist). A US short code typically carries on the order of 100 messages/second;
  the contracted figure is unknown. Carriers count segments.
- Real volume: millions of messages a month, with a Sunday-morning peak. Assume
  peak day in the hundreds of thousands until measured. Design headroom of 10x
  is welcome; 100x is not a requirement.
- Must keep working during the change: no big-bang cutover. Churches keep
  sending every week.
- Mistakes that matter most: a person who texted STOP gets another text; a
  duplicate blast to thousands; a private reply filed under the wrong church;
  a Sunday 8am message arriving at 11.
- Team is small (assume 2 to 3 engineers plus part-time platform help).

## What exists (all readable on this machine, read-only)
Root: `C:\code\communications\Legacy code\`
- `HGS-MessagingService` — the sender. VB.NET Windows Service on .NET Framework
  4.7.1. `Messaging.ServiceV1\MessagingServiceCore.vb` polls SQL every 10 s
  (`sp_Messaging_GetAlerts`), reserves an alert, then one thread builds the
  recipient list and sends inline. `CallQueue.vb` (`buildCdyneSMSQueue`, line
  2352) does several SQL round trips per recipient (opt-out check, insert into
  `call_queue`, insert recipient link), sends 2,000 recipients per SOAP call to
  Esendex via `PrimeMessageSender.vb`, then one lookup and insert per recipient
  into `text_message_response`. Merge-field messages force batch size 1. On a
  result-write error it sleeps 30 s and resends the whole batch. Up to 60
  concurrent alerts; no resume after a crash mid-alert.
- `HGS-KeywordServiceWebApp` — provider callbacks and inbound SMS. VB.NET Web
  Forms on .NET Framework 4.6.2, Azure App Service. `cdyne_sms_response.aspx.vb`
  handles delivery receipts and inbound replies (STOP/START/HELP, keywords,
  two-way reply attribution in `AppCode\utilities.vb`). Sleeps 60 s when a
  receipt arrives before the send result is stored. Has a switch to copy
  callbacks to an Azure Service Bus topic.
- `HGS-HGSCoreApi` — .NET 6 APIs and Azure Functions (vendor callback queue
  processor, SMS failover API, partner API).
- `HGS-PWA` — the current product: .NET 10 API (`HGS.Api`) and a React web app
  (`HGS.Web`), actively developed. Already has a partner iframe embed
  (`ParishSoftController.cs`) and a Kafka consumer for Amplify people sync.
- `HGS-HighgroundProviders` — shared NuGet libraries, including an Azure Service
  Bus publisher and receiver.
- Database: Azure SQL (`schoolcast_live`), shared by everything. Key tables:
  `alerts`, `call_queue`, `call_recipients`, `text_message_response`,
  `sms_inbound`, `sms_optout`, `sms_long_codes`, `sms_providers`, `customers`,
  `contacts`, `optin_keywords`.
- Hosting is Azure: App Service, Azure Functions, Azure SQL, Service Bus,
  Storage Queues. Kafka is used for one integration.

Also on this machine: `C:\code\communications\PLAN.md` (the current plan,
revision 5; its sections 5.2, 5.3, 5.3.1, 5.7 and P1-19 hold correctness rules
that came out of earlier reviews and still apply) and `C:\code\communications\app\`
(a separate Python/FastAPI + Postgres + Redis prototype of a new engine, which
the "not a rewrite" decision has put in doubt).

## Rules for every participant
- Read the legacy code before asserting anything about it. Cite file and line.
- Separate what you verified from what you are assuming. Mark unknowns.
- No generic advice. Every recommendation names the component, the technology,
  and why it beats the alternatives for this system and this team.
- Do not edit any file under `C:\code\communications`. Write only where your
  instructions say.
- Address the shared short code honestly: no architecture on our side raises
  the carrier rate. Say what your design does when demand exceeds it.
