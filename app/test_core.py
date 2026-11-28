"""The checks that matter. No framework, no fixtures, no database.

These cover the three places where being wrong is expensive:
  1. the SOAP envelope (a wrong field name = every send fails)
  2. inbound classification (a wrong match = silently unsubscribing people)
  3. the MessageID / MatchedMessageID swap (joins threads to the wrong alert)

Run: python -m app.test_core
"""
from __future__ import annotations

from app.audience import chunked
from app.esendex import (
    ARRAY_NS,
    BATCH,
    CONTENT_TYPE,
    DC_NS,
    NS,
    SOAP_ACTION,
    SOAP_ENV,
    WSA_NS,
    build_envelope,
    classify_inbound,
    has_unicode,
    parse_response,
    postback_kind,
    resolve_inbound_ids,
)

KEY = "11111111-2222-3333-4444-555555555555"


def test_envelope_matches_the_wsdl() -> None:
    """Every value here was read from https://sms2tls.esendex.us/sms.svc?wsdl
    and confirmed with a live HTTP 200. Changing any of them breaks every send,
    and the failure is a SOAP fault, not an obvious error."""
    assert NS == "http://sms2.cdyne.com"
    assert SOAP_ACTION == "http://sms2.cdyne.com/Isms/AdvancedSMSsend"
    assert DC_NS == "http://schemas.datacontract.org/2004/07/SmsWS"

    xml = build_envelope(
        license_key=KEY,
        body="Rehearsal moved to 7pm.",
        phones=["+15135550101", "+15135550102"],
        reference_id="b42-0",
        postback_url="https://example.test/webhooks/esendex",
        assigned_did="+15135559999",
        to_url="https://sms2tls.esendex.us/sms.svc/WS",
    )
    # The wrapper parameter is AdvancedRequest. Calling it "request" earns a
    # ContractFilter mismatch at the EndpointDispatcher.
    assert "<AdvancedRequest" in xml
    assert '<AdvancedSMSsend xmlns="http://sms2.cdyne.com">' in xml
    assert DC_NS in xml and ARRAY_NS in xml
    assert xml.count("<a:string>") == 2
    assert "+15135559999" in xml


def test_ws_addressing_header_present() -> None:
    """wsHttpBinding enables WS-Addressing, so Action and To are mandatory.
    Without them: a:ActionMismatch, HTTP 500."""
    xml = build_envelope(
        license_key=KEY, body="hi", phones=["1"], reference_id="r",
        postback_url="u", to_url="https://sms2tls.esendex.us/sms.svc/WS",
    )
    assert WSA_NS in xml
    assert "<s:Header>" in xml
    assert '<w:Action s:mustUnderstand="1">' + SOAP_ACTION + "</w:Action>" in xml
    assert "https://sms2tls.esendex.us/sms.svc/WS</w:To>" in xml


def test_field_order_is_alphabetical() -> None:
    """DataContractSerializer emits members alphabetically, so the schema's
    xs:sequence is alphabetical too and the service rejects any other order.

    This is the check most likely to be "cleaned up" by someone grouping the
    fields logically. Do not. The order is the contract.
    """
    xml = build_envelope(
        license_key=KEY, body="hi", phones=["1"], reference_id="r",
        postback_url="u", assigned_did="+1555",
    )
    outer = ["Concatenate", "IsUnicode", "LicenseKey", "SMSRequests"]
    inner = ["AssignedDID", "Message", "PhoneNumbers", "ReferenceID",
             "ScheduledDateTime", "StatusPostBackURL"]
    for seq in (outer, inner):
        at = [xml.index("<d:" + f) for f in seq]
        assert at == sorted(at), f"{seq} must appear in this order; got {at}"


def test_envelope_is_soap_12() -> None:
    """The live endpoint rejects SOAP 1.1 with HTTP 415. Confirmed, not guessed:
    `sms2wsHttpBinding` is WCF wsHttpBinding, which is SOAP 1.2. If anyone
    "simplifies" this back to text/xml, every send fails."""
    xml = build_envelope(
        license_key=KEY, body="hi", phones=["1"], reference_id="r", postback_url="u"
    )
    assert SOAP_ENV == "http://www.w3.org/2003/05/soap-envelope"
    assert SOAP_ENV in xml
    assert "schemas.xmlsoap.org/soap/envelope" not in xml, "that is SOAP 1.1, refused with 415"
    assert CONTENT_TYPE.startswith("application/soap+xml")
    # In 1.2 the action rides in Content-Type, not a SOAPAction header.
    assert 'action="' + SOAP_ACTION + '"' in CONTENT_TYPE


def test_envelope_flags() -> None:
    short = build_envelope(
        license_key=KEY, body="x" * 100, phones=["1"], reference_id="r",
        postback_url="u",
    )
    assert "<d:Concatenate>false</d:Concatenate>" in short
    assert "<d:IsUnicode>false</d:IsUnicode>" in short

    long = build_envelope(
        license_key=KEY, body="x" * 200, phones=["1"], reference_id="r",
        postback_url="u",
    )
    assert "<d:Concatenate>true</d:Concatenate>" in long

    emoji = build_envelope(
        license_key=KEY, body="See you Sunday ❤", phones=["1"], reference_id="r",
        postback_url="u",
    )
    assert "<d:IsUnicode>true</d:IsUnicode>" in emoji


def test_envelope_escapes_and_bounds() -> None:
    xml = build_envelope(
        license_key=KEY,
        body="Pancakes & prayer <today>",
        phones=["1"],
        reference_id="r",
        postback_url="u",
    )
    assert "&amp;" in xml and "&lt;today&gt;" in xml
    assert "<today>" not in xml, "unescaped body would break the envelope"

    for bad, why in ((0, "empty"), (BATCH + 1, "over batch")):
        try:
            build_envelope(
                license_key=KEY, body="hi", phones=["1"] * bad, reference_id="r",
                postback_url="u",
            )
        except ValueError:
            pass
        else:
            raise AssertionError(f"{why} recipient list should have been rejected")


def test_parse_response_is_positional() -> None:
    xml = """<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/"><s:Body>
      <AdvancedSMSsendResponse xmlns="http://ws.cdyne.com/sms/"><AdvancedSMSsendResult>
        <SMSResponse><MessageID>aaa-1</MessageID><SMSError>NoError</SMSError></SMSResponse>
        <SMSResponse><MessageID></MessageID><SMSError>InvalidPhoneNumber</SMSError></SMSResponse>
      </AdvancedSMSsendResult></AdvancedSMSsendResponse>
    </s:Body></s:Envelope>"""
    out = parse_response(xml, ["+15135550101", "+1555"])
    assert len(out) == 2
    assert out[0].phone == "+15135550101" and out[0].ok and out[0].provider_msg_id == "aaa-1"
    assert out[1].phone == "+1555" and not out[1].ok
    assert out[1].error == "InvalidPhoneNumber"


def test_parse_response_survives_garbage() -> None:
    out = parse_response("<<not xml", ["+1", "+2"])
    assert len(out) == 2 and not any(r.ok for r in out)
    # Fewer responses than recipients must not raise or silently drop rows.
    out = parse_response("<Envelope></Envelope>", ["+1", "+2"])
    assert len(out) == 2 and not any(r.ok for r in out)


def test_classify_inbound_is_exact_not_prefix() -> None:
    assert classify_inbound("STOP") == "stop"
    assert classify_inbound(" stop ") == "stop"
    assert classify_inbound("Stop.") == "stop"
    assert classify_inbound("START") == "start"
    assert classify_inbound("help") == "help"
    assert classify_inbound("Can you help me find the nursery?") == "reply"
    assert classify_inbound("yes please") == "reply"


def test_classify_inbound_optout_rules() -> None:
    # Opt-out wrapped in filler is still an opt-out (legacy opts these out).
    for body in ("please stop", "Stop texting me", "STOP ALL", "opt-out", "Unsub", "arret", "block"):
        assert classify_inbound(body) == "stop", body
    # A poll answer must never re-subscribe anyone.
    for body in ("yes", "Yes!", "subscribe", "optin"):
        assert classify_inbound(body) == "reply", body
    assert classify_inbound("resume") == "start"
    assert classify_inbound("hlp") == "help"
    # Looks like an opt-out but is not exact: hold for a person, never
    # auto-unsubscribe (the potluck) and never ignore (the real request).
    for body in ("stop by the potluck at 6", "take me off this list", "don't text me again", "wrong number"):
        assert classify_inbound(body) == "review", body
    # Ordinary words that are also opt-out words do not trip it mid-sentence.
    assert classify_inbound("see you at the end of service") == "reply"


def test_inbound_id_trap() -> None:
    # With MatchedMessageID present, the live handler overwrites messageId.
    # These two must stay distinguishable or threads join to the wrong alert.
    inbound, matched = resolve_inbound_ids(
        {"MessageID": "in-99", "MatchedMessageID": "out-42"}
    )
    assert inbound == "in-99"
    assert matched == "out-42"
    assert inbound != matched

    inbound, matched = resolve_inbound_ids({"MessageID": "in-99"})
    assert inbound == "in-99" and matched is None

    inbound, matched = resolve_inbound_ids({"MessageID": "", "MatchedMessageID": "  "})
    assert inbound is None and matched is None


def test_postback_branches() -> None:
    assert postback_kind({"SMSResponse": "1"}) == "inbound"
    assert postback_kind({"DeliveryReceipt": "1"}) == "dlr"
    assert postback_kind({"SMSSent": "1"}) == "accepted"
    assert postback_kind({}) == "unknown"


def test_unicode_detection() -> None:
    assert not has_unicode("Plain ASCII only")
    assert has_unicode("café")
    assert has_unicode("\U0001f64f")


def test_chunking_covers_everything() -> None:
    items = list(range(4501))
    chunks = list(chunked(items, 2000))
    assert [len(c) for c in chunks] == [2000, 2000, 501]
    flat = [x for c in chunks for x in c]
    assert flat == items, "chunking must not drop or reorder recipients"
    assert list(chunked([], 2000)) == []


def demo() -> None:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"  ok  {t.__name__}")
    print(f"\n{len(tests)} checks passed")


if __name__ == "__main__":
    demo()
