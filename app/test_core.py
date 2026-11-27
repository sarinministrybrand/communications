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
    BATCH,
    build_envelope,
    classify_inbound,
    has_unicode,
    parse_response,
    postback_kind,
    resolve_inbound_ids,
)

KEY = "11111111-2222-3333-4444-555555555555"


def test_envelope_shape() -> None:
    xml = build_envelope(
        license_key=KEY,
        body="Rehearsal moved to 7pm.",
        phones=["+15135550101", "+15135550102"],
        reference_id="b42-0",
        postback_url="https://example.test/webhooks/esendex",
        assigned_did="+15135559999",
    )
    # Every field the shipping sender sets must be present.
    for field in (
        "LicenseKey",
        "IsUnicode",
        "Concatenate",
        "SMSRequests",
        "Message",
        "PhoneNumbers",
        "ScheduledDateTime",
        "AssignedDID",
        "ReferenceID",
        "StatusPostBackURL",
    ):
        assert f"<t:{field}>" in xml, f"envelope is missing {field}"
    assert xml.count("<a:string>") == 2
    assert "+15135559999" in xml


def test_envelope_flags() -> None:
    short = build_envelope(
        license_key=KEY, body="x" * 100, phones=["1"], reference_id="r",
        postback_url="u",
    )
    assert "<t:Concatenate>false</t:Concatenate>" in short
    assert "<t:IsUnicode>false</t:IsUnicode>" in short

    long = build_envelope(
        license_key=KEY, body="x" * 200, phones=["1"], reference_id="r",
        postback_url="u",
    )
    assert "<t:Concatenate>true</t:Concatenate>" in long

    emoji = build_envelope(
        license_key=KEY, body="See you Sunday ❤", phones=["1"], reference_id="r",
        postback_url="u",
    )
    assert "<t:IsUnicode>true</t:IsUnicode>" in emoji


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
    # The expensive one: a prefix match here unsubscribes a member who was
    # only talking about the potluck.
    assert classify_inbound("stop by the potluck at 6") == "reply"
    assert classify_inbound("Can you help me find the nursery?") == "reply"
    assert classify_inbound("yes please") == "reply"


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
