from src.app.subscription_postback import decode_postback_payload, encode_subscription_payload


def test_decode_subscription_payload_roundtrip():
    payload = {"kind": "cancel", "group_id": "group-123"}
    encoded = encode_subscription_payload(payload)

    decoded = decode_postback_payload(encoded)

    assert decoded == payload
