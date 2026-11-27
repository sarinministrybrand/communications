"""Esendex / CDYNE SMS adapter.

Field names and semantics are taken from the shipping sender,
HGS-MessagingService `Messaging.ServiceV1/PrimeMessageSender.vb:239-290`, which
builds `SMSAdvancedRequest { LicenseKey, IsUnicode, Concatenate, SMSRequests[] }`
where each `SMSRequest` carries
`{ Message, PhoneNumbers[], ScheduledDateTime, AssignedDID, ReferenceID, StatusPostBackURL }`.

One SMSRequest carries up to BATCH recipients, so a 1e9/day send is ~500k
provider calls, not 1e9. That ratio is the whole reason this is affordable.

The wire format is VERIFIED against the live endpoint, not assumed. The shipping
code never writes an envelope by hand -- it binds a WCF client by the endpoint
*name* `sms2wsHttpBinding`, so the format is generated from the WSDL and is
invisible in that repo. It was recovered by reading
`https://sms2tls.esendex.us/sms.svc?wsdl` plus its `?xsd=xsd0` / `?xsd=xsd2`
imports, and confirmed with a live HTTP 200.

Four things the service rejects, each found the hard way:
  1. SOAP 1.1 envelope / `text/xml`      -> HTTP 415 (wsHttpBinding is SOAP 1.2)
  2. no WS-Addressing Action + To header -> a:ActionMismatch
  3. action `ws.cdyne.com/sms/...`       -> ContractFilter mismatch
     (the real one is `sms2.cdyne.com/Isms/...`, portType `Isms`)
  4. fields in any non-alphabetical order -> schema violation
     (DataContractSerializer sorts members, so xs:sequence is alphabetical)
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

# All four values below are read from the live WSDL at
# https://sms2tls.esendex.us/sms.svc?wsdl (and its ?xsd=xsd0 / ?xsd=xsd2
# imports), not from documentation. Earlier guesses at `ws.cdyne.com/sms/` were
# wrong and the service rejected them -- see the fault history in the README.
NS = "http://sms2.cdyne.com"                                   # service contract
SOAP_ACTION = "http://sms2.cdyne.com/Isms/AdvancedSMSsend"     # portType Isms
DC_NS = "http://schemas.datacontract.org/2004/07/SmsWS"        # the data contract
ARRAY_NS = "http://schemas.microsoft.com/2003/10/Serialization/Arrays"

# SOAP 1.2, confirmed against the live endpoint. A SOAP 1.1 envelope
# (http://schemas.xmlsoap.org/soap/envelope/, text/xml, separate SOAPAction
# header) is rejected with:
#   HTTP 415 "Cannot process the message because the content type
#   'text/xml; charset=utf-8' was not the expected type
#   'application/soap+xml; charset=utf-8'."
# This was inferable from the shipping config without asking the provider: the
# WCF client is built by the binding NAME `sms2wsHttpBinding`, and WCF's
# wsHttpBinding is SOAP 1.2. In 1.2 the action travels as a Content-Type
# parameter rather than its own header.
SOAP_ENV = "http://www.w3.org/2003/05/soap-envelope"
CONTENT_TYPE = 'application/soap+xml; charset=utf-8; action="' + SOAP_ACTION + '"'

# WS-Addressing 1.0. wsHttpBinding enables it by default, so the Action and To
# headers are mandatory -- without them the service faults with a:ActionMismatch
# even when the Content-Type action is correct.
WSA_NS = "http://www.w3.org/2005/08/addressing"
DEFAULT_SEND_URL = "https://sms2tls.esendex.us/sms.svc/WS"

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
    to_url: str = DEFAULT_SEND_URL,
) -> str:
    """Build one AdvancedSMSsend envelope.

    `assigned_did` empty means the shared short code; set means the church's
    dedicated long code, matching `req.AssignedDID` in the shipping sender.

    `to_url` fills the WS-Addressing `To` header and must be the endpoint the
    request is actually posted to, or the service rejects it.
    """
    if not phones:
        raise ValueError("no recipients")
    if len(phones) > BATCH:
        raise ValueError(f"{len(phones)} recipients exceeds provider batch {BATCH}")

    when = (scheduled_at or datetime.now(timezone.utc)).astimezone(timezone.utc)
    numbers = "".join("<a:string>" + escape(p) + "</a:string>" for p in phones)
    is_unicode = "true" if has_unicode(body) else "false"
    concatenate = "true" if len(body) > SINGLE_SEGMENT else "false"

    # Field order below is NOT arbitrary and must not be "tidied". WCF's
    # DataContractSerializer emits members alphabetically, so the schema's
    # xs:sequence is alphabetical, and a sequence out of order is rejected:
    #   SMSAdvancedRequest -> Concatenate, IsUnicode, LicenseKey, SMSRequests
    #   SMSRequest         -> AssignedDID, Message, PhoneNumbers, ReferenceID,
    #                         ScheduledDateTime, StatusPostBackURL
    # The wrapper parameter is `AdvancedRequest`, not `request`.
    return (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<s:Envelope xmlns:s="' + SOAP_ENV + '" xmlns:w="' + WSA_NS + '">'
        # WS-Addressing header. wsHttpBinding turns this on by default, and the
        # service refuses the message without it:
        #   a:ActionMismatch -- "The SOAP action specified on the message, '',
        #   does not match the HTTP SOAP Action".
        # mustUnderstand="1" is what the service's own fault envelope uses.
        "<s:Header>"
        '<w:Action s:mustUnderstand="1">' + SOAP_ACTION + "</w:Action>"
        '<w:To s:mustUnderstand="1">' + escape(to_url) + "</w:To>"
        "</s:Header>"
        "<s:Body>"
        '<AdvancedSMSsend xmlns="' + NS + '">'
        '<AdvancedRequest xmlns:d="' + DC_NS + '">'
        "<d:Concatenate>" + concatenate + "</d:Concatenate>"
        "<d:IsUnicode>" + is_unicode + "</d:IsUnicode>"
        "<d:LicenseKey>" + escape(license_key) + "</d:LicenseKey>"
        "<d:SMSRequests><d:SMSRequest>"
        "<d:AssignedDID>" + escape(assigned_did) + "</d:AssignedDID>"
        "<d:Message>" + escape(body) + "</d:Message>"
        '<d:PhoneNumbers xmlns:a="' + ARRAY_NS + '">' + numbers + "</d:PhoneNumbers>"
        "<d:ReferenceID>" + escape(reference_id) + "</d:ReferenceID>"
        "<d:ScheduledDateTime>" + when.strftime("%Y-%m-%dT%H:%M:%SZ") + "</d:ScheduledDateTime>"
        "<d:StatusPostBackURL>" + escape(postback_url) + "</d:StatusPostBackURL>"
        "</d:SMSRequest></d:SMSRequests>"
        "</AdvancedRequest></AdvancedSMSsend></s:Body></s:Envelope>"
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
            # WS-Addressing To must equal the URL we actually post to.
            to_url=self.send_url,
        )
        # SOAP 1.2 carries the action inside Content-Type; sending a separate
        # SOAPAction header alongside it is what 1.1 does and is refused here.
        headers = {"Content-Type": CONTENT_TYPE}

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
