"""Esendex / CDYNE SMS adapter.

Field names and semantics are taken from the shipping sender,
HGS-MessagingService `Messaging.ServiceV1/PrimeMessageSender.vb:239-290`, which
builds `SMSAdvancedRequest { LicenseKey, IsUnicode, Concatenate, SMSRequests[] }`
where each `SMSRequest` carries
`{ Message, PhoneNumbers[], ScheduledDateTime, AssignedDID, ReferenceID, StatusPostBackURL }`.

One SMSRequest carries up to BATCH recipients, so a 1e9/day send is ~500k
provider calls, not 1e9. That ratio is the whole reason this is affordable.

CONFIRM BEFORE PRODUCTION: the SOAP namespace and SOAPAction below are the
documented CDYNE values. The shipping code never writes an envelope by hand --
it binds a WCF client by the endpoint *name* `sms2wsHttpBinding`, so the wire
format is generated from the WSDL and is not visible in that repo. Fetch
`<send_url>?wsdl` once and diff element order before the first live send.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING
from xml.sax.saxutils import escape

if TYPE_CHECKING:
    import httpx

log = logging.getLogger(__name__)

NS = "http://ws.cdyne.com/sms/"
SOAP_ACTION = "http://ws.cdyne.com/sms/AdvancedSMSsend"
ARRAY_NS = "http://schemas.microsoft.com/2003/10/Serialization/Arrays"

# PrimeMessageSender sends one chunk per call; cdyne_sms_batch_size = 2000.
BATCH = 2000

# GSM-7 single segment. Past this the provider concatenates and bills per
# segment, which is exactly where competitors' credit maths bites hardest.
SINGLE_SEGMENT = 160


@dataclass(slots=True)
class SendResult:
    phone: str
    provider_msg_id: str | None
    ok: bool
    error: str | None = None


def has_unicode(text: str) -> bool:
    """Mirrors Utilities.ContainsUnicodeCharacter. Any non-ASCII character
    flips the whole message to UCS-2, which cuts the segment to 70 chars."""
    return any(ord(c) > 127 for c in text)


def build_envelope(
    *,
    license_key: str,
    body: str,
    phones: list[str],
    reference_id: str,
    postback_url: str,
    assigned_did: str = "",
    scheduled_at: datetime | None = None,
) -> str:
    """Build one AdvancedSMSsend envelope.

    `assigned_did` empty means the shared short code; set means the church's
    dedicated long code, matching `req.AssignedDID` in the shipping sender.
    """
    if not phones:
        raise ValueError("no recipients")
    if len(phones) > BATCH:
        raise ValueError(f"{len(phones)} recipients exceeds provider batch {BATCH}")

    when = (scheduled_at or datetime.now(timezone.utc)).astimezone(timezone.utc)
    numbers = "".join("<a:string>" + escape(p) + "</a:string>" for p in phones)
    is_unicode = "true" if has_unicode(body) else "false"
    concatenate = "true" if len(body) > SINGLE_SEGMENT else "false"

    return (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/"'
        ' xmlns:t="' + NS + '" xmlns:a="' + ARRAY_NS + '">'
        "<s:Body><t:AdvancedSMSsend><t:request>"
        "<t:LicenseKey>" + escape(license_key) + "</t:LicenseKey>"
        "<t:IsUnicode>" + is_unicode + "</t:IsUnicode>"
        "<t:Concatenate>" + concatenate + "</t:Concatenate>"
        "<t:SMSRequests><t:SMSRequest>"
        "<t:Message>" + escape(body) + "</t:Message>"
        "<t:PhoneNumbers>" + numbers + "</t:PhoneNumbers>"
        "<t:ScheduledDateTime>" + when.strftime("%Y-%m-%dT%H:%M:%SZ") + "</t:ScheduledDateTime>"
        "<t:AssignedDID>" + escape(assigned_did) + "</t:AssignedDID>"
        "<t:ReferenceID>" + escape(reference_id) + "</t:ReferenceID>"
        "<t:StatusPostBackURL>" + escape(postback_url) + "</t:StatusPostBackURL>"
        "</t:SMSRequest></t:SMSRequests>"
        "</t:request></t:AdvancedSMSsend></s:Body></s:Envelope>"
    )


def parse_response(xml: str, phones: list[str]) -> list[SendResult]:
    """Pull one SMSResponse per recipient, in request order.

    The provider returns the array positionally, so index i corresponds to
    phones[i]. Parsed with the stdlib because the shape is three fields deep
    and a SOAP library would be a dependency for nothing.
    """
    import xml.etree.ElementTree as ET

    try:
        root = ET.fromstring(xml)
    except ET.ParseError as exc:
        return [SendResult(p, None, False, "unparseable response: " + str(exc)) for p in phones]

    responses = [el for el in root.iter() if el.tag.endswith("SMSResponse")]

    out: list[SendResult] = []
    for i, phone in enumerate(phones):
        if i >= len(responses):
            out.append(SendResult(phone, None, False, "no response element"))
            continue
        el = responses[i]

        def field(name: str) -> str | None:
            for child in el:
                if child.tag.endswith(name):
                    return (child.text or "").strip()
            return None

        msg_id = field("MessageID")
        status = (field("SMSError") or field("ResponseText") or "").strip()
        ok = bool(msg_id) and status.lower() in ("", "noerror", "queued", "success")
        out.append(SendResult(phone, msg_id, ok, None if ok else (status or "rejected")))
    return out


class EsendexClient:
    def __init__(self, send_url: str, postback_url: str, timeout: float = 600.0):
        import httpx

        self.send_url = send_url
        self.postback_url = postback_url
        # Timeout matches the shipping WCF binding: all four timeouts 00:10:00.
        self._http = httpx.AsyncClient(
            timeout=timeout,
            limits=httpx.Limits(max_connections=200, max_keepalive_connections=50),
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    async def send_batch(
        self,
        *,
        license_key: str,
        body: str,
        phones: list[str],
        reference_id: str,
        assigned_did: str = "",
        attempts: int = 3,
    ) -> list[SendResult]:
        """Send one batch, retrying transport failures with backoff.

        The shipping code swallows send exceptions into an error email and does
        not retry, which silently drops whole batches. This retries. A duplicate
        send still costs money, so `attempts` stays small and only transport
        errors and 5xx are retried -- never a 4xx, which retrying cannot fix.
        """
        envelope = build_envelope(
            license_key=license_key,
            body=body,
            phones=phones,
            reference_id=reference_id,
            postback_url=self.postback_url,
            assigned_did=assigned_did,
        )
        headers = {"Content-Type": "text/xml; charset=utf-8", "SOAPAction": SOAP_ACTION}

        last = "not attempted"
        for attempt in range(1, attempts + 1):
            try:
                resp = await self._http.post(self.send_url, content=envelope, headers=headers)
                if resp.status_code == 200:
                    return parse_response(resp.text, phones)
                if resp.status_code < 500:
                    return [
                        SendResult(p, None, False, "HTTP " + str(resp.status_code))
                        for p in phones
                    ]
                last = "HTTP " + str(resp.status_code)
            except Exception as exc:  # httpx transport/timeout; imported lazily
                last = type(exc).__name__ + ": " + str(exc)

            log.warning(
                "esendex batch retry %d/%d ref=%s: %s", attempt, attempts, reference_id, last
            )
            if attempt < attempts:
                await asyncio.sleep(2 ** attempt)

        return [SendResult(p, None, False, last) for p in phones]


# ----------------------------------------------------------------- inbound
# Postback contract, from CallMonitorServiceWebApp/cdyne_sms_response.aspx.vb:
#   MessageID, SMSResponse, MatchedMessageID, ReferenceID,
#   FromPhoneNumber, ToPhoneNumber, Message, ResponseReceiveDate
# Three mutually exclusive flags pick the branch:
#   SMSSent         -> send accepted
#   DeliveryReceipt -> handset delivery receipt
#   SMSResponse     -> an actual inbound reply

STOP_WORDS = {"stop", "stopall", "unsubscribe", "cancel", "end", "quit", "optout", "opt-out"}
START_WORDS = {"start", "yes", "unstop", "subscribe", "optin", "opt-in"}
HELP_WORDS = {"help", "info"}


def classify_inbound(body: str) -> str:
    """Carrier-mandated keywords first; everything else is a reply.

    Matched on the whole trimmed body, never as a prefix: "stop by the potluck
    at 6" is a reply, not an opt-out. A prefix match here silently
    unsubscribes people, which is both a support burden and a compliance claim
    you cannot defend.
    """
    word = body.strip().lower().strip(".!,?")
    if word in STOP_WORDS:
        return "stop"
    if word in START_WORDS:
        return "start"
    if word in HELP_WORDS:
        return "help"
    return "reply"


def resolve_inbound_ids(params: dict) -> tuple:
    """Return (inbound_message_id, outbound_message_id_being_answered).

    THE TRAP: the live handler overwrites its `messageId` local with
    `MatchedMessageID` when that parameter is present, so the value which
    actually lands in `sms_inbound.message_id` is the id of the OUTBOUND
    message, not the inbound one. It is therefore not a unique key for the
    inbound row. Code that assumes otherwise joins every thread to the wrong
    alert. Keep the two values apart -- that is all this function does.
    """
    inbound_id = (params.get("MessageID") or "").strip() or None
    matched = (params.get("MatchedMessageID") or "").strip() or None
    return inbound_id, matched


def postback_kind(params: dict) -> str:
    """Which of the three postback branches this request is."""
    if (params.get("SMSResponse") or "").strip():
        return "inbound"
    if (params.get("DeliveryReceipt") or "").strip():
        return "dlr"
    if (params.get("SMSSent") or "").strip():
        return "accepted"
    return "unknown"
