import json
import hmac
import hashlib
from unittest.mock import patch
from app.config import settings

def get_signature(body: bytes, key: str) -> str:
    sig = hmac.new(key.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return f"sha256={sig}"

def test_valid_signature(client):
    payload = {
        "event_id": "evt_sig_ok",
        "event_type": "comment.created",
        "sent_at": "2026-08-10T09:14:22.481Z",
        "data": {"comment_id": "cmt_sig_ok", "from": {"user_id": "usr1", "username": "u1"}}
    }
    body = json.dumps(payload).encode("utf-8")
    headers = {"X-PseudoGram-Signature": get_signature(body, settings.PSEUDOGRAM_API_KEY)}
    
    response = client.post("/webhook", data=body, headers=headers)
    assert response.status_code == 200
    assert response.json() == {"status": "accepted"}

def test_invalid_signature(client):
    payload = {
        "event_id": "evt_sig_bad",
        "event_type": "comment.created",
        "sent_at": "2026-08-10T09:14:22.481Z",
        "data": {"comment_id": "cmt_sig_bad", "from": {"user_id": "usr1", "username": "u1"}}
    }
    body = json.dumps(payload).encode("utf-8")
    headers = {"X-PseudoGram-Signature": get_signature(body, "wrong_api_key")}
    
    response = client.post("/webhook", data=body, headers=headers)
    assert response.status_code == 401
    assert "Invalid" in response.json()["detail"]

def test_missing_signature(client):
    payload = {
        "event_id": "evt_sig_missing",
        "event_type": "comment.created",
        "sent_at": "2026-08-10T09:14:22.481Z",
        "data": {"comment_id": "cmt_sig_missing", "from": {"user_id": "usr1", "username": "u1"}}
    }
    body = json.dumps(payload).encode("utf-8")
    
    response = client.post("/webhook", data=body)
    assert response.status_code == 401
    assert "missing" in response.json()["detail"].lower()

def test_simulator_style_email_secret_signature(client):
    # API key with structured format
    test_key = "cGFyYXN1cHJhZGVlcDNAZ21haWwuY29t.d1b3554797b51fb4e4dd"
    email_secret = "parasupradeep3@gmail.com"
    
    payload = {
        "event_id": "evt_sim_ok",
        "event_type": "comment.created",
        "sent_at": "2026-08-10T09:14:22.481Z",
        "data": {"comment_id": "cmt_sim_ok", "from": {"user_id": "usr1", "username": "u1"}}
    }
    body = json.dumps(payload).encode("utf-8")
    
    # Sign with base64-decoded email secret
    sig = hmac.new(email_secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    headers = {"X-PseudoGram-Signature": f"sha256={sig}"}
    
    with patch("app.routes.webhook.settings.PSEUDOGRAM_API_KEY", test_key):
        response = client.post("/webhook", data=body, headers=headers)
        assert response.status_code == 200
        assert response.json() == {"status": "accepted"}

def test_simulator_style_invalid_signature(client):
    test_key = "cGFyYXN1cHJhZGVlcDNAZ21haWwuY29t.d1b3554797b51fb4e4dd"
    wrong_email_secret = "other_user@gmail.com"
    
    payload = {
        "event_id": "evt_sim_bad",
        "event_type": "comment.created",
        "sent_at": "2026-08-10T09:14:22.481Z",
        "data": {"comment_id": "cmt_sim_bad", "from": {"user_id": "usr1", "username": "u1"}}
    }
    body = json.dumps(payload).encode("utf-8")
    
    sig = hmac.new(wrong_email_secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    headers = {"X-PseudoGram-Signature": f"sha256={sig}"}
    
    with patch("app.routes.webhook.settings.PSEUDOGRAM_API_KEY", test_key):
        response = client.post("/webhook", data=body, headers=headers)
        assert response.status_code == 401
        assert "Invalid" in response.json()["detail"]

def test_simulator_style_malformed_signature(client):
    test_key = "cGFyYXN1cHJhZGVlcDNAZ21haWwuY29t.d1b3554797b51fb4e4dd"
    
    payload = {
        "event_id": "evt_sim_malformed",
        "event_type": "comment.created",
        "sent_at": "2026-08-10T09:14:22.481Z",
        "data": {"comment_id": "cmt_sim_malformed", "from": {"user_id": "usr1", "username": "u1"}}
    }
    body = json.dumps(payload).encode("utf-8")
    
    # Signature is not a valid hex string or missing sha256= prefix
    headers = {"X-PseudoGram-Signature": "sha256=invalid-signature-hex"}
    
    with patch("app.routes.webhook.settings.PSEUDOGRAM_API_KEY", test_key):
        response = client.post("/webhook", data=body, headers=headers)
        assert response.status_code == 401
        assert "Invalid" in response.json()["detail"]

def test_raw_body_used_unchanged(client):
    test_key = "cGFyYXN1cHJhZGVlcDNAZ21haWwuY29t.d1b3554797b51fb4e4dd"
    email_secret = "parasupradeep3@gmail.com"
    
    # Body containing extra spaces (not formatted/minified JSON)
    spaced_body_str = '{"event_id":   "evt_spaced",   "event_type": "comment.created", "sent_at": "2026-08-10T09:14:22.481Z", "data": {"comment_id": "cmt_spaced", "from": {"user_id": "usr1", "username": "u1"}}}'
    body_bytes = spaced_body_str.encode("utf-8")
    
    # If we sign using the exact raw bytes of the spaced body
    sig_ok = hmac.new(email_secret.encode("utf-8"), body_bytes, hashlib.sha256).hexdigest()
    headers_ok = {"X-PseudoGram-Signature": f"sha256={sig_ok}"}
    
    with patch("app.routes.webhook.settings.PSEUDOGRAM_API_KEY", test_key):
        response_ok = client.post("/webhook", data=body_bytes, headers=headers_ok)
        assert response_ok.status_code == 200
        
    # If we sign a canonicalized/minified JSON instead of the raw spaced bytes
    compact_payload = json.loads(spaced_body_str)
    compact_bytes = json.dumps(compact_payload, separators=(',', ':')).encode("utf-8")
    sig_bad = hmac.new(email_secret.encode("utf-8"), compact_bytes, hashlib.sha256).hexdigest()
    headers_bad = {"X-PseudoGram-Signature": f"sha256={sig_bad}"}
    
    with patch("app.routes.webhook.settings.PSEUDOGRAM_API_KEY", test_key):
        # We send the spaced bytes but with the signature computed on compact bytes
        response_bad = client.post("/webhook", data=body_bytes, headers=headers_bad)
        assert response_bad.status_code == 401
